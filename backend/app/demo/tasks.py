"""Génération des données de démonstration (voir app/demo/__init__.py)."""
import json
import logging
import math
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from influxdb_client import Point, WritePrecision

from app.core.database import SessionLocal
from app.core.influx import INFLUXDB_BUCKET, INFLUXDB_ORG, get_influx
from app.core.redis_client import LATEST_KEY, UPDATES_CHANNEL, r_client
from app.demo import DEMO_SUFFIX
from app.models.application import VM, Application
from app.models.inventory import InfrastructureDevice, LatencyAnomaly, MetricHistory, ServiceDiscovery
from app.tasks.celery_app import celery_app
from app.tasks.scheduler import _check_latency, _store_capacity_history, _store_timeseries, _sync_vm_inventory

logger = logging.getLogger(__name__)

# (nom, hôte, type, catégorie, port)
DEVICES = [
    ("vCenter PROD Douala", "vcsa-prod", "VCENTER", "VIRTUALIZATION", 443),
    ("vCenter PRA Yaoundé", "vcsa-pra", "VCENTER", "VIRTUALIZATION", 443),
    ("Dorado 5000 — Makepe", "dorado-mkp", "HUAWEI_DORADO", "STORAGE", 8088),
    ("Unity 480 — Bonanjo", "unity-bnj", "DELL_UNITY", "STORAGE", 443),
    ("PowerStore 1200T", "pstore", "DELL_POWERSTORE", "STORAGE", 443),
    ("DataDomain DD6900", "dd6900", "DATADOMAIN", "BACKUP", 3009),
    ("NetBackup Primary", "nbu-primary", "NETBACKUP", "BACKUP", 1556),
    ("Scality RING S3", "ring", "SCALITY", "OBJECT", 443),
    ("Veritas Access NAS", "vaccess", "VERITAS_ACCESS", "STORAGE", 14161),
    ("vRealize Operations", "vrops", "VROPS", "VIRTUALIZATION", 443),
]
START_KEY = "noc_demo_start"
APPS = ["sap", "crm", "web", "db", "k8s"]
OSES = ["RHEL 9.2", "Windows Server 2022", "Ubuntu 22.04", "SLES 15 SP5"]


def _host(short: str) -> str:
    return f"{short}{DEMO_SUFFIX}"


def _wave(t: float, period_min: float, phase: float = 0.0) -> float:
    """Oscillation douce entre -1 et 1 selon l'heure (données cohérentes d'une minute à l'autre)."""
    return math.sin(t / (period_min * 60) * 2 * math.pi + phase)


def _vms(vcenter_host: str, prod: bool) -> List[Dict[str, Any]]:
    count, prefix = (28, "PROD") if prod else (12, "PRA")
    base = 0 if prod else 28
    return [{
        "index": base + i,
        "vm_name": f"vm-{APPS[(base + i) % 5]}-{base + i:02d}",
        "vcenter": vcenter_host,
        "cluster": f"CL-{prefix}-{i % 2 + 1:02d}",
        "os": OSES[(base + i) % len(OSES)],
        "ip": f"10.20.{(base + i) // 10}.{10 + base + i}",
    } for i in range(count)]


def _vm_metrics(vm: Dict[str, Any], t: float) -> Dict[str, float]:
    i = vm["index"]
    saturated = i in (3, 11)  # deux VMs saturées en CPU en permanence
    cpu = 95.5 + 3 * abs(_wave(t, 7, i)) if saturated else 25 + 30 * abs(_wave(t, 90 + i * 7, i))
    latency_peak = i == 7 and _wave(t, 240) > 0.3  # une VM avec des pics de latence disque
    read = 22 + 12 * abs(_wave(t, 9, 1)) if latency_peak else 1.2 + abs(_wave(t, 30, i))
    return {
        "cpu_usage_pct": round(min(cpu, 100), 1),
        "ram_usage_pct": round(40 + (i * 7) % 50 + 3 * _wave(t, 60, i), 1),
        "storage_gb": float(80 + i * 7),
        "read_latency_ms": round(read, 2),
        "write_latency_ms": round(read * 0.6, 2),
    }


def _vcenter_payload(name: str, host: str, prod: bool, t: float) -> Dict[str, Any]:
    vms = _vms(host, prod)
    vm_rows = [{**{k: v for k, v in vm.items() if k != "index"}, **_vm_metrics(vm, t)} for vm in vms]
    n_hosts = 8 if prod else 4
    hosts = [{
        "host_name": f"esx{n:02d}-{'prod' if prod else 'pra'}{DEMO_SUFFIX}",
        "cluster": f"CL-{'PROD' if prod else 'PRA'}-{n % 2 + 1:02d}",
        "status": "MAINTENANCE" if prod and n == 6 else "CONNECTED",
        "cpu_usage_pct": round(45 + 30 * abs(_wave(t, 120, n)), 1) if prod else round(18 + 10 * abs(_wave(t, 120, n)), 1),
        "ram_usage_pct": round(70 + 18 * abs(_wave(t, 300, n)), 1) if prod else round(45 + 8 * abs(_wave(t, 300, n)), 1),
    } for n in range(1, n_hosts + 1)]
    ds_usage = [91.2, 84.5, 72.0, 65.3, 58.9, 41.0] if prod else [61.0, 48.2, 39.5]
    datastores = []
    for n, u in enumerate(ds_usage, 1):
        # DS-PROD-02 : pics de latence périodiques au-dessus du seuil (20 ms par défaut).
        spike = prod and n == 2 and _wave(t, 180) > 0.5
        read = round(24 + 6 * abs(_wave(t, 11)), 2) if spike else round(1.5 + 2.5 * abs(_wave(t, 45, n)), 2)
        write = round(read * 0.7, 2)
        datastores.append({
            "datastore_name": f"DS-{'PROD' if prod else 'PRA'}-{n:02d}", "capacity_tb": 20.0,
            "used_tb": round(20 * u / 100, 2), "usage_pct": u,
            "read_latency_ms": read, "write_latency_ms": write, "latency_ms": max(read, write),
        })
    filesystems = []
    for vm in vms[:20]:
        for mount, cap, used in (("/", 50.0, 20 + vm["index"] * 1.4), ("/var/lib/data", 500.0, 300 + vm["index"] * 9)):
            used = min(used, cap * 0.985)
            filesystems.append({"vm_name": vm["vm_name"], "vcenter": host, "cluster": vm["cluster"], "mount_point": mount,
                                "capacity_gb": cap, "used_gb": round(used, 1), "free_gb": round(cap - used, 1),
                                "usage_pct": round(used / cap * 100, 1)})
    cpu = round(sum(h["cpu_usage_pct"] for h in hosts) / len(hosts), 1)
    ram = round(sum(h["ram_usage_pct"] for h in hosts) / len(hosts), 1)
    total_tb = 20.0 * len(datastores)
    used_tb = round(sum(d["used_tb"] for d in datastores), 2)
    alerts = []
    if ram > 85:
        alerts.append({"severity": "WARNING", "message": f"Charge RAM cluster élevée : {ram}%", "component": "Cluster-ESXi"})
    for d in datastores:
        if d["usage_pct"] >= 90:
            alerts.append({"severity": "WARNING", "message": f"{d['datastore_name']} à {d['usage_pct']} % de capacité", "component": "Datastores"})
    return {
        "device_type": "virtualization",
        "status": "WARNING" if alerts else "HEALTHY",
        "metrics": {
            "cpu_usage_pct": cpu, "ram_usage_pct": ram, "vms_total": len(vms) * 11, "vms_active": len(vms) * 10,
            "hosts_total": n_hosts, "hosts_online": n_hosts - (1 if prod else 0), "hosts_maintenance": 1 if prod else 0,
            "datastores_total_tb": total_tb, "datastores_used_tb": used_tb,
            "datastores_usage_pct": round(used_tb / total_tb * 100, 1),
            "latency_ms": max(d["latency_ms"] for d in datastores),
        },
        "alerts": alerts,
        "hosts_metrics": hosts,
        "datastores_metrics": datastores,
        "vms_metrics": vm_rows,
        "vms_fs_metrics": filesystems,
    }


def _storage_payload(dtype: str, t: float, days: float) -> Dict[str, Any]:
    """Baies / sauvegarde / objet. `days` = jours écoulés depuis le début de la démo (croissance)."""
    w = lambda p, ph=0.0: _wave(t, p, ph)  # noqa: E731
    if dtype == "HUAWEI_DORADO":
        used = 189 + 0.45 * days
        m = {"iops": round(45000 + 9000 * w(80)), "latency_ms": round(0.42 + 0.1 * abs(w(30)), 2),
             "throughput_mb": round(1800 + 300 * w(60), 1), "capacity_total_tb": 300.0, "capacity_used_tb": round(used, 2)}
    elif dtype == "DELL_UNITY":
        used = min(140.1 + 0.52 * days, 149.0)
        m = {"iops": round(12000 + 3000 * w(70, 1)), "latency_ms": round(3.6 + 0.6 * abs(w(20)), 2),
             "throughput_mb": round(410 + 80 * w(50), 1), "capacity_total_tb": 150.0, "capacity_used_tb": round(used, 2)}
    elif dtype == "DELL_POWERSTORE":
        used = 39.0 + 0.02 * days
        m = {"iops": round(30000 + 6000 * w(90, 2)), "latency_ms": round(0.6 + 0.1 * abs(w(25)), 2),
             "throughput_mb": round(950 + 120 * w(45), 1), "data_reduction_ratio": 3.9,
             "capacity_total_tb": 80.0, "capacity_used_tb": round(used, 2)}
    elif dtype == "DATADOMAIN":
        used = 359.5 + 0.65 * days
        m = {"dedup_ratio": 14.2, "active_streams": round(30 + 10 * abs(w(40))),
             "capacity_total_tb": 500.0, "capacity_used_tb": round(used, 2)}
    elif dtype == "NETBACKUP":
        failed = round(60 + 15 * abs(w(180)))
        m = {"backup_success_rate": round(100 - failed / 1284 * 100, 1), "jobs_total": 1284, "jobs_failed": failed,
             "jobs_active": round(15 + 10 * abs(w(35))), "daily_protected_tb": 41.3}
        return {"device_type": "backup", "status": "WARNING", "metrics": m,
                "alerts": [{"severity": "WARNING", "message": f"{failed} jobs en échec sur les dernières 24 h", "component": "Jobs"}]}
    elif dtype == "SCALITY":
        used = 236.4 + 0.25 * days
        m = {"iops_s3": round(2200 + 600 * w(50)), "active_nodes": 6, "total_nodes": 6,
             "capacity_total_tb": 400.0, "capacity_used_tb": round(used, 2)}
    else:  # VERITAS_ACCESS
        used = 61.0 + 0.08 * days
        m = {"nfs_shares_active": 42, "cifs_shares_active": 17, "cluster_nodes_online": 2, "cluster_nodes_total": 2,
             "capacity_total_tb": 120.0, "capacity_used_tb": round(used, 2)}
    m["capacity_usage_pct"] = round(m["capacity_used_tb"] / m["capacity_total_tb"] * 100, 1)
    category = {"DATADOMAIN": "backup", "SCALITY": "object"}.get(dtype, "storage")
    alerts, status = [], "HEALTHY"
    if m["capacity_usage_pct"] >= 90:
        status = "CRITICAL"
        alerts.append({"severity": "CRITICAL", "component": "Storage Pool",
                       "message": f"Pool à {m['capacity_usage_pct']} % — saturation imminente"})
    return {"device_type": category, "status": status, "metrics": m, "alerts": alerts}


def _vrops_payload(t: float, days: float) -> Dict[str, Any]:
    """Même structure que VRopsCollector : clusters, datastores, rightsizing, alertes vROps."""
    clusters = []
    for i, (name, base, vms) in enumerate([("CL-PROD-01", 64, 14), ("CL-PROD-02", 71, 14), ("CL-PRA-01", 28, 6), ("CL-PRA-02", 33, 6)]):
        cpu = round(base * 0.7 + 6 * _wave(t, 90, i), 1)
        ram = round(base + 4 * _wave(t, 240, i), 1)
        remaining = max(5.0, round((140 if i < 2 else 400) - 40 * i - days, 1))
        clusters.append({"name": name, "cpu_usage_pct": cpu, "ram_usage_pct": ram, "time_remaining_days": remaining,
                         "capacity_remaining_pct": round(100 - ram, 0), "health": [100, 75, 100, 100][i],
                         "risk": [25, 50, 0, 25][i], "efficiency": [50, 25, 75, 75][i], "running_vms": vms})
    datastores = []
    for i, (name, cap, used) in enumerate([("DS-PROD-01", 20.0, 17.4), ("DS-PROD-02", 20.0, 13.1),
                                           ("DS-PRA-01", 12.0, 5.2), ("DS-PRA-02", 12.0, 4.4)]):
        used = round(used + 0.05 * days, 2)
        datastores.append({"name": name, "capacity_tb": cap, "used_tb": used, "usage_pct": round(used / cap * 100, 1),
                           "time_remaining_days": max(3.0, round([24, 160, 420, 500][i] - days, 1))})
    datastores.sort(key=lambda d: d["usage_pct"], reverse=True)
    rightsizing = (
        [{"vm_name": f"vm-{APPS[i % 5]}-{i:02d}", "kind": "oversized", "vcpus": 2 + i % 3 * 2, "memory_gb": float(8 + i % 4 * 8)} for i in (1, 4, 7, 9, 12, 15, 21)]
        + [{"vm_name": f"vm-{APPS[i % 5]}-{i:02d}", "kind": "undersized", "vcpus": 2, "memory_gb": 4.0} for i in (3, 18)]
        + [{"vm_name": f"vm-{APPS[i % 5]}-{i:02d}", "kind": "idle"} for i in (30, 33, 37)]
    )
    min_days = min(r["time_remaining_days"] for r in clusters + datastores)
    alerts = [
        {"severity": "CRITICAL", "component": "esx-prod-07", "message": "Host has lost redundant connectivity to a dvPort"},
        {"severity": "WARNING", "component": "DS-PROD-01", "message": "Datastore is running out of disk space (immédiate)"},
        {"severity": "WARNING", "component": "vm-sap-01", "message": "Virtual machine has CPU contention caused by co-stop"},
        {"severity": "WARNING", "component": "CL-PROD-02", "message": "Cluster has memory contention caused by overcommit"},
    ]
    if min_days < 30:
        alerts.insert(0, {"severity": "WARNING", "component": "Capacité", "message": f"Saturation prévue par vROps dans {min_days:g} jour(s)"})
    metrics = {
        "clusters_total": 4, "hosts_total": 16, "vms_total": 40,
        "alerts_critical": 1, "alerts_immediate": 1, "alerts_warning": 2,
        "vms_oversized": 7, "vms_undersized": 2, "vms_idle": 3,
        "capacity_time_remaining_days": min_days,
        "datastores_usage_pct": round(sum(d["used_tb"] for d in datastores) / sum(d["capacity_tb"] for d in datastores) * 100, 1),
    }
    return {"device_type": "operations", "status": "WARNING", "metrics": metrics, "alerts": alerts,
            "clusters_metrics": clusters, "vrops_datastores": datastores, "rightsizing": rightsizing}


def _payload(dev: InfrastructureDevice, t: float, days: float) -> Dict[str, Any]:
    if dev.device_type == "VROPS":
        return _vrops_payload(t, days)
    if dev.device_type == "VCENTER":
        return _vcenter_payload(dev.display_name, dev.hostname_ip, dev.hostname_ip.startswith("vcsa-prod"), t)
    return _storage_payload(dev.device_type, t, days)


# ── Initialisation (une seule fois) ─────────────────────────────────────────
def _create_device(db, name: str, short: str, dtype: str, category: str, port: int) -> InfrastructureDevice:
    dev = InfrastructureDevice(display_name=name, hostname_ip=_host(short), device_type=dtype, category=category,
                               port=port, username="svc_demo_ro", is_active=True, last_sync_status="UNSYNCED")
    dev.set_password("demo")
    db.add(dev)
    db.flush()
    # Inscrit mais inactif dans le Service Discovery : le répartiteur réel ne le collecte jamais.
    db.add(ServiceDiscovery(device_id=dev.id, is_active=False))
    return dev


def _add_new_devices(db, existing: List[InfrastructureDevice]) -> None:
    """Démo déjà initialisée par une version antérieure : ajoute seulement les nouveaux types."""
    known = {d.hostname_ip for d in existing}
    added = [_create_device(db, *spec) for spec in DEVICES if _host(spec[1]) not in known]
    if added:
        db.commit()
        logger.info("Démo : %d équipement(s) ajouté(s) (%s).", len(added), ", ".join(d.display_name for d in added))


def _seed(db) -> None:
    now = datetime.now(timezone.utc)
    devices = []
    for spec in DEVICES:
        devices.append(_create_device(db, *spec))
    db.commit()

    # 45 jours d'historique capacitaire (1 point toutes les 6 h) et 24 h de séries temporelles.
    rnd = random.Random(42)
    points = []
    for dev in devices:
        for k in range(45 * 4, 0, -1):
            ts = now - timedelta(hours=6 * k)
            p = _payload(dev, ts.timestamp(), -k / 4)
            m = p["metrics"]
            if dev.device_type == "VCENTER":
                rows = [("CPU", "%", 100.0, m["cpu_usage_pct"]), ("RAM", "%", 100.0, m["ram_usage_pct"]),
                        ("Datastores", "TB", m["datastores_total_tb"], m["datastores_used_tb"] - 0.02 * k)]
            elif "capacity_total_tb" in m:
                rows = [("Capacité globale", "TB", m["capacity_total_tb"], m["capacity_used_tb"] + rnd.uniform(-0.3, 0.3))]
            else:
                rows = []
            for res, unit, total, used in rows:
                db.add(MetricHistory(device_id=dev.id, timestamp=ts.replace(tzinfo=None), resource_name=res, unit=unit,
                                     capacity_total_tb=total, capacity_used_tb=used,
                                     capacity_free_tb=max(0.0, total - used), usage_pct=used / total * 100))
        for k in range(288, 0, -1):  # 24 h, pas de 5 min
            ts = now - timedelta(minutes=5 * k)
            p = _payload(dev, ts.timestamp(), 0)
            pt = Point("noc_telemetry").tag("device_id", dev.id).tag("device_type", p["device_type"]).time(ts, WritePrecision.S)
            for f, v in p["metrics"].items():
                if isinstance(v, (int, float)):
                    pt.field(f, float(v))
            points.append(pt)
            for vm in p.get("vms_metrics", []):
                vp = (Point("vm_telemetry").tag("device_id", dev.id).tag("vm_name", vm["vm_name"])
                      .tag("vcenter", vm["vcenter"]).tag("cluster", vm["cluster"]).time(ts, WritePrecision.S))
                for f in ("cpu_usage_pct", "ram_usage_pct", "storage_gb", "read_latency_ms", "write_latency_ms"):
                    vp.field(f, float(vm[f]))
                points.append(vp)
            for ds in p.get("datastores_metrics", []):
                dp = (Point("ds_telemetry").tag("device_id", dev.id).tag("datastore_name", ds["datastore_name"])
                      .field("usage_pct", float(ds["usage_pct"])).time(ts, WritePrecision.S))
                for f in ("read_latency_ms", "write_latency_ms"):
                    if f in ds:
                        dp.field(f, float(ds[f]))
                points.append(dp)
    influx = get_influx()
    for i in range(0, len(points), 5000):
        influx.write_api.write(bucket=INFLUXDB_BUCKET, org=INFLUXDB_ORG, record=points[i:i + 5000])

    prod = next(d for d in devices if d.hostname_ip.startswith("vcsa-prod"))
    db.add(LatencyAnomaly(device_id=prod.id, target_name="DS-PROD-01", latency_ms=34.7, threshold_ms=20.0,
                          timestamp=datetime.utcnow() - timedelta(minutes=42)))
    db.add(LatencyAnomaly(device_id=prod.id, target_name="DS-PROD-02", latency_ms=22.1, threshold_ms=20.0,
                          timestamp=datetime.utcnow() - timedelta(hours=5), is_resolved=True))
    db.commit()
    logger.info("Données de démonstration initialisées : %d équipements, %d points InfluxDB.", len(devices), len(points))


def _ensure_application(db) -> None:
    if db.query(Application).filter(Application.name == "SAP ERP (démo)").first():
        return
    vms = db.query(VM).filter(VM.name.like("vm-sap-%")).all()
    if vms:
        db.add(Application(name="SAP ERP (démo)", description="Application de démonstration", vms=vms))
        db.commit()


# ── Rafraîchissement (chaque minute) ────────────────────────────────────────
@celery_app.task(name="app.demo.tasks.refresh_demo_data", ignore_result=True)
def refresh_demo_data() -> int:
    with SessionLocal() as db:
        demo = db.query(InfrastructureDevice).filter(InfrastructureDevice.hostname_ip.like(f"%{DEMO_SUFFIX}")).all()
        if not demo:
            _seed(db)
        else:
            _add_new_devices(db, demo)
        demo = db.query(InfrastructureDevice).filter(InfrastructureDevice.hostname_ip.like(f"%{DEMO_SUFFIX}")).all()

    now = datetime.now(timezone.utc)
    # Date de début de la démo : sert à faire croître les capacités de façon continue.
    r_client.set(START_KEY, now.timestamp(), nx=True)
    days = (now.timestamp() - float(r_client.get(START_KEY) or now.timestamp())) / 86400
    refreshed = 0
    for dev in demo:
        if dev.is_archived or not dev.is_active:
            continue
        payload = _payload(dev, now.timestamp(), days)
        payload.update(device_id=dev.id, device_name=dev.display_name, collected_at=now.isoformat(), demo=True)
        _store_timeseries(dev.id, payload)
        _sync_vm_inventory(payload)
        _store_capacity_history(dev.id, payload)
        _check_latency(dev.id, dev.display_name, payload)  # même règle de seuil que les vraies mesures
        r_client.set(LATEST_KEY.format(device_id=dev.id), json.dumps(payload, default=str), ex=3600)
        refreshed += 1

    with SessionLocal() as db:
        for dev in db.query(InfrastructureDevice).filter(InfrastructureDevice.hostname_ip.like(f"%{DEMO_SUFFIX}")).all():
            if dev.is_active and not dev.is_archived:
                dev.last_sync_status, dev.last_sync_at, dev.last_sync_error = "ONLINE", datetime.utcnow(), None
        db.commit()
        _ensure_application(db)

    r_client.publish(UPDATES_CHANNEL, json.dumps({"type": "DEMO_REFRESH", "timestamp": now.isoformat()}))
    return refreshed
