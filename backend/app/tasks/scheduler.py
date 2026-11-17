"""
Pipeline de collecte.

    celery beat ──15 s──> dispatch_polling ──> collect_device(id)  (une tâche par équipement)

- `dispatch_polling` lit l'intervalle de collecte en base à chaque passage : un changement
  dans l'interface s'applique sans redémarrage.
- `collect_device` prend un verrou Redis par équipement : deux collectes du même équipement
  ne peuvent pas se chevaucher, et un équipement lent ne bloque plus les autres.
- Seuls les workers contactent les équipements ; l'API ne lit que Redis et les bases.
"""
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from celery.exceptions import SoftTimeLimitExceeded

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.influx import get_influx
from app.core.redis_client import (
    HISTORY_GATE_KEY, LATEST_KEY, LOCK_KEY, NEXT_POLL_KEY, UPDATES_CHANNEL, r_client,
)
from app.core.smtp import send_latency_alert_email
from app.demo import DEMO_SUFFIX
from app.models.application import VM
from app.models.inventory import InfrastructureDevice, LatencyAnomaly, MetricHistory, PasswordDecryptionError, ServiceDiscovery
from app.services.collectors import COLLECTOR_REGISTRY
from app.services.settings_service import get_polling_interval, get_setting
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

HISTORY_EVERY_S = 3600  # un point d'historique capacitaire SQL par heure et par équipement


# ── Répartiteur ─────────────────────────────────────────────────────────────
@celery_app.task(name="app.tasks.scheduler.dispatch_polling")
def dispatch_polling() -> int:
    with SessionLocal() as db:
        interval = get_polling_interval(db)
        device_ids = [
            d.id for d in db.query(InfrastructureDevice)
            .join(ServiceDiscovery, ServiceDiscovery.device_id == InfrastructureDevice.id)
            .filter(ServiceDiscovery.is_active.is_(True),
                    InfrastructureDevice.is_active.is_(True),
                    InfrastructureDevice.is_archived.is_(False))
            .all()
        ]
        demo_ids = {
            d.id for d in db.query(InfrastructureDevice.id)
            .filter(InfrastructureDevice.hostname_ip.like(f"%{DEMO_SUFFIX}")).all()
        }
    queued = 0
    for device_id in device_ids:
        if device_id in demo_ids:
            continue  # équipement fictif du mode démo : jamais de vraie collecte
        # SET NX : vrai seulement si aucune collecte n'est prévue pour cet équipement.
        if r_client.set(NEXT_POLL_KEY.format(device_id=device_id), "1", nx=True, ex=interval):
            collect_device.delay(device_id)
            queued += 1
    if queued:
        logger.info("Répartiteur : %d collecte(s) lancée(s) sur %d équipement(s).", queued, len(device_ids))
    return queued


# ── Collecte d'un équipement ────────────────────────────────────────────────
@celery_app.task(
    name="app.tasks.scheduler.collect_device",
    soft_time_limit=settings.collector_timeout_s,
    time_limit=settings.collector_timeout_s + 30,
)
def collect_device(device_id: str) -> None:
    lock_key = LOCK_KEY.format(device_id=device_id)
    if not r_client.set(lock_key, "1", nx=True, ex=settings.collector_timeout_s + 60):
        logger.info("Collecte de %s déjà en cours, ignorée.", device_id)
        return
    try:
        _collect(device_id)
    finally:
        r_client.delete(lock_key)


def _collect(device_id: str) -> None:
    with SessionLocal() as db:
        device = db.get(InfrastructureDevice, device_id)
        if not device or device.is_archived or not device.is_active:
            return
        collector_class = COLLECTOR_REGISTRY.get(device.device_type)
        if not collector_class:
            _set_sync(db, device, "ERROR", f"Type non supporté : {device.device_type}")
            return
        try:
            password = device.get_password()
        except PasswordDecryptionError:
            # Ne PAS tenter de connexion sans mot de passe (risque de verrouillage du compte).
            _set_sync(db, device, "ERROR", "Mot de passe indéchiffrable : ressaisissez-le dans l'inventaire.")
            return
        config = {
            "simulate": False, "host": device.hostname_ip, "port": device.port,
            "username": device.username, "password": password,
        }
        name, dtype, interval = device.display_name, device.device_type, get_polling_interval(db)

    collector = collector_class(device_id, name, config)
    try:
        payload = collector.collect()
    except SoftTimeLimitExceeded:
        payload = _error_payload(device_id, name, f"Délai de collecte dépassé ({settings.collector_timeout_s} s)")
    except Exception as exc:  # un collecteur ne doit jamais faire tomber le worker
        logger.exception("Collecte de %s en échec", name)
        payload = _error_payload(device_id, name, f"{type(exc).__name__}: {exc}")

    payload["device_id"] = device_id
    payload["device_name"] = name
    payload["collected_at"] = datetime.now(timezone.utc).isoformat()
    ok = payload.get("status") not in ("OFFLINE", "ERROR")

    if ok:
        _store_timeseries(device_id, payload)
        _sync_vm_inventory(payload)
        _store_capacity_history(device_id, payload)
        _check_latency(device_id, name, payload)

    # Dernier état : conservé assez longtemps pour que l'interface affiche « données périmées »
    # plutôt qu'un équipement vide si une collecte échoue.
    r_client.set(LATEST_KEY.format(device_id=device_id), json.dumps(payload, default=str),
                 ex=max(interval * 10, 3600))

    with SessionLocal() as db:
        device = db.get(InfrastructureDevice, device_id)
        if device:
            error = None if ok else "; ".join(a.get("message", "") for a in payload.get("alerts", []))[:500] or "Collecte en échec"
            _set_sync(db, device, "ONLINE" if ok else "OFFLINE", error)

    r_client.publish(UPDATES_CHANNEL, json.dumps({
        "type": "DEVICE_UPDATED", "device_id": device_id, "status": payload.get("status"),
        "timestamp": payload["collected_at"],
    }))
    logger.info("Collecte %s : %s (%s)", name, payload.get("status"), dtype)


def _error_payload(device_id: str, name: str, message: str) -> Dict[str, Any]:
    return {
        "device_id": device_id, "device_name": name, "status": "ERROR", "metrics": {},
        "alerts": [{"severity": "CRITICAL", "message": message, "component": "Collecteur"}],
    }


def _set_sync(db, device: InfrastructureDevice, status: str, error: str | None) -> None:
    device.last_sync_status = status
    device.last_sync_at = datetime.utcnow()
    device.last_sync_error = error
    db.commit()


# ── Routage des données ─────────────────────────────────────────────────────
def _store_timeseries(device_id: str, payload: Dict[str, Any]) -> None:
    influx = get_influx()
    influx.write_metrics(device_id, payload.get("device_type", "unknown"), payload.get("metrics", {}))
    if payload.get("vms_metrics"):
        influx.write_vm_telemetry(device_id, payload["vms_metrics"])
    if payload.get("vms_fs_metrics"):
        influx.write_vm_fs_telemetry(device_id, payload["vms_fs_metrics"])
    if payload.get("datastores_metrics"):
        influx.write_ds_telemetry(device_id, payload["datastores_metrics"])


def _sync_vm_inventory(payload: Dict[str, Any]) -> None:
    vms = [v for v in payload.get("vms_metrics") or [] if v.get("vm_name")]
    if not vms:
        return
    try:
        with SessionLocal() as db:
            existing = {vm.name: vm for vm in db.query(VM).filter(VM.name.in_([v["vm_name"] for v in vms])).all()}
            for v in vms:
                ip = v.get("ip") if v.get("ip") not in (None, "", "N/A") else None
                os_name = v.get("os") if v.get("os") not in (None, "", "Unknown OS") else None
                vm = existing.get(v["vm_name"])
                if vm is None:
                    vm = VM(name=v["vm_name"])
                    db.add(vm)
                    existing[v["vm_name"]] = vm
                vm.vcenter = v.get("vcenter") or "inconnu"
                vm.cluster = v.get("cluster") or "inconnu"
                vm.ip = ip or vm.ip
                vm.os = os_name or vm.os
            db.commit()
    except Exception:
        logger.exception("Synchronisation de l'inventaire des VMs en échec")


def _store_capacity_history(device_id: str, payload: Dict[str, Any]) -> None:
    """Historique capacitaire SQL : un point par heure (la v2 en écrivait un par minute, sans fin)."""
    if not r_client.set(HISTORY_GATE_KEY.format(device_id=device_id), "1", nx=True, ex=HISTORY_EVERY_S - 60):
        return
    m = payload.get("metrics", {})
    now = datetime.utcnow()
    entries = []

    def add(resource: str, unit: str, total: Any, used: Any) -> None:
        try:
            total_f, used_f = float(total), float(used)
        except (TypeError, ValueError):
            return
        if total_f <= 0:
            return
        entries.append(MetricHistory(
            device_id=device_id, timestamp=now, resource_name=resource, unit=unit,
            capacity_total_tb=total_f, capacity_used_tb=used_f,
            capacity_free_tb=max(0.0, total_f - used_f), usage_pct=round(used_f / total_f * 100, 2),
        ))

    if payload.get("device_type") == "virtualization":
        add("CPU", "%", 100, m.get("cpu_usage_pct"))
        add("RAM", "%", 100, m.get("ram_usage_pct"))
        add("Datastores", "TB", m.get("datastores_total_tb"), m.get("datastores_used_tb"))
    else:
        add("Capacité globale", "TB", m.get("capacity_total_tb"), m.get("capacity_used_tb"))
        if m.get("subscription_pct") is not None:
            add("Souscription", "%", 100, m.get("subscription_pct"))
    if not entries:
        return
    try:
        with SessionLocal() as db:
            db.add_all(entries)
            db.commit()
    except Exception:
        logger.exception("Écriture de l'historique capacitaire en échec")


def _check_latency(device_id: str, device_name: str, payload: Dict[str, Any]) -> None:
    """Alerte sur latence de datastore RÉELLEMENT mesurée (champ absent = pas d'alerte).
    Complète `payload` (alertes, statut) avant son enregistrement dans Redis."""
    datastores = [ds for ds in payload.get("datastores_metrics") or [] if ds.get("latency_ms") is not None]
    if not datastores:
        return
    with SessionLocal() as db:
        try:
            threshold = float(get_setting(db, "latency_threshold_ms"))
        except ValueError:
            threshold = 20.0
        since = datetime.utcnow() - timedelta(hours=1)
        for ds in datastores:
            latency = float(ds["latency_ms"])
            if latency <= threshold:
                continue
            # Alerte visible tant que la latence reste au-dessus du seuil (vue d'ensemble, centre d'alertes).
            payload.setdefault("alerts", []).append({
                "severity": "WARNING",
                "component": "Datastores",
                "message": f"Latence {ds['datastore_name']} : {latency:.1f} ms (seuil {threshold:g} ms)",
            })
            if payload.get("status") == "HEALTHY":
                payload["status"] = "WARNING"
            # Historique + e-mail : au plus une fois par heure et par datastore.
            recent = db.query(LatencyAnomaly).filter(
                LatencyAnomaly.device_id == device_id,
                LatencyAnomaly.target_name == ds["datastore_name"],
                LatencyAnomaly.timestamp >= since,
            ).first()
            if recent:
                continue
            db.add(LatencyAnomaly(device_id=device_id, target_name=ds["datastore_name"],
                                  latency_ms=latency, threshold_ms=threshold))
            db.commit()
            send_latency_alert.delay(device_name, ds["datastore_name"], latency, threshold)


@celery_app.task(name="app.tasks.scheduler.send_latency_alert", autoretry_for=(OSError,), retry_backoff=True, max_retries=3)
def send_latency_alert(device_name: str, target_name: str, latency_ms: float, threshold_ms: float) -> None:
    """Envoi d'e-mail dans une tâche séparée : ne ralentit plus la collecte."""
    send_latency_alert_email(device_name, target_name, latency_ms, threshold_ms)
