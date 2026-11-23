"""
Accès InfluxDB : écriture de la télémétrie et requêtes de lecture.

Toutes les valeurs venant de l'utilisateur sont référencées dans les requêtes par
`params._xxx` ; l'enregistrement `params` est déclaré en tête de requête avec des
littéraux strictement échappés (voir build_query) : plus d'injection possible.
Les noms de champs sont en plus contrôlés par liste blanche.
En cas d'erreur, les fonctions renvoient une liste vide — jamais de valeur inventée.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

from app.core.config import settings

logger = logging.getLogger(__name__)

INFLUXDB_BUCKET = settings.influxdb_bucket
INFLUXDB_ORG = settings.influxdb_org

# Champs autorisés en lecture, par mesure.
DEVICE_FIELDS = {
    "cpu_usage_pct", "ram_usage_pct", "datastores_usage_pct", "vms_total", "vms_active",
    "hosts_total", "hosts_online", "iops", "latency_ms", "throughput_mb", "capacity_usage_pct",
    "capacity_used_tb", "capacity_total_tb", "dedup_ratio", "active_streams", "backup_success_rate",
    "jobs_failed", "jobs_active", "iops_s3", "active_nodes", "data_reduction_ratio",
    "nfs_shares_active", "cifs_shares_active",
    # vRealize Operations
    "clusters_total", "alerts_critical", "alerts_immediate", "alerts_warning",
    "vms_oversized", "vms_undersized", "vms_idle", "capacity_time_remaining_days",
    "vms_contention", "reclaimable_vcpus", "reclaimable_memory_gb", "snapshots_gb",
}
VM_FIELDS = {"cpu_usage_pct", "ram_usage_pct", "storage_gb", "read_latency_ms", "write_latency_ms"}
DS_FIELDS = {"usage_pct", "read_latency_ms", "write_latency_ms"}
CLUSTER_FIELDS = {"cpu_usage_pct", "cpu_demand_pct", "ram_usage_pct", "time_remaining_days", "health", "risk", "efficiency"}
OBJECT_TYPES = {
    "Virtual Machine": ("vm_telemetry", "vm_name", VM_FIELDS),
    "Datastore": ("ds_telemetry", "datastore_name", DS_FIELDS),
    "Cluster": ("cluster_telemetry", "cluster_name", CLUSTER_FIELDS),
}


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def flux_string(value: str) -> str:
    """Littéral chaîne Flux sûr : guillemets, antislash, interpolation `${` et caractères
    de contrôle sont échappés — une valeur ne peut jamais sortir de sa chaîne."""
    out = []
    for ch in value:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "$":
            out.append("\\x24")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\x{ord(ch):02x}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def flux_literal(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("nombre invalide")
        return repr(value)
    if isinstance(value, datetime):
        return _utc(value).strftime("%Y-%m-%dT%H:%M:%S.%fZ")  # littéral date RFC3339
    if isinstance(value, timedelta):
        return f"{max(1, int(value.total_seconds()))}s"
    if isinstance(value, str):
        return flux_string(value)
    if isinstance(value, (list, tuple, set)):
        return "[" + ", ".join(flux_literal(v) for v in value) + "]"
    raise TypeError(f"Type de paramètre Flux non supporté : {type(value).__name__}")


def build_query(flux: str, params: Dict[str, Any]) -> str:
    """Insère `params = {...}` après les `import` (qui doivent rester en tête)."""
    for key in params:
        if not key.replace("_", "").isalnum():
            raise ValueError(f"Nom de paramètre invalide : {key}")
    record = "params = {" + ", ".join(f"{k}: {flux_literal(v)}" for k, v in params.items()) + "}"
    lines = flux.strip("\n").splitlines()
    head = [ln for ln in lines if ln.strip().startswith("import ")]
    body = [ln for ln in lines if not ln.strip().startswith("import ")]
    return "\n".join(head + [record] + body)


def _window(hours: Optional[int] = None, start: Optional[datetime] = None,
            end: Optional[datetime] = None) -> Dict[str, datetime]:
    stop = _utc(end) if end else datetime.now(timezone.utc)
    begin = _utc(start) if start else stop - timedelta(hours=hours or 1)
    return {"_start": begin, "_stop": stop}


class InfluxDBManager:
    def __init__(self) -> None:
        self.client = InfluxDBClient(
            url=settings.influxdb_url, token=settings.influxdb_token, org=INFLUXDB_ORG, timeout=20_000
        )
        self.write_api = self.client.write_api(write_options=SYNCHRONOUS)
        self.query_api = self.client.query_api()

    # ── Requête générique ───────────────────────────────────────────────────
    def _query(self, flux: str, params: Dict[str, Any]) -> list:
        """Exécute `flux` en définissant l'enregistrement `params` en tête de requête.
        (InfluxDB OSS n'accepte pas le paramètre `params` de l'API, réservé à InfluxDB Cloud :
        on déclare donc nous-mêmes `params = {...}` avec des littéraux strictement échappés.)"""
        params = {"_bucket": INFLUXDB_BUCKET, **params}
        try:
            return self.query_api.query(query=build_query(flux, params), org=INFLUXDB_ORG)
        except Exception as exc:
            logger.error("Requête InfluxDB en échec : %s", exc)
            return []

    # ── Écriture ────────────────────────────────────────────────────────────
    def _write(self, points: List[Point], what: str) -> None:
        if not points:
            return
        try:
            self.write_api.write(bucket=INFLUXDB_BUCKET, org=INFLUXDB_ORG, record=points)
        except Exception as exc:
            logger.error("Écriture InfluxDB (%s) en échec : %s", what, exc)

    def write_metrics(self, device_id: str, device_type: str, metrics: Dict[str, Any]) -> None:
        point = Point("noc_telemetry").tag("device_id", device_id).tag("device_type", device_type)
        has_field = False
        for name, val in metrics.items():
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                point.field(name, float(val))
                has_field = True
        if has_field:
            self._write([point.time(datetime.now(timezone.utc), WritePrecision.S)], "noc_telemetry")

    def write_vm_telemetry(self, device_id: str, vms: List[Dict[str, Any]]) -> None:
        now = datetime.now(timezone.utc)
        points = []
        for vm in vms:
            p = (Point("vm_telemetry").tag("device_id", device_id).tag("vm_name", vm["vm_name"])
                 .tag("vcenter", vm.get("vcenter") or "").tag("cluster", vm.get("cluster") or "")
                 .time(now, WritePrecision.S))
            for f in VM_FIELDS:
                if isinstance(vm.get(f), (int, float)):
                    p.field(f, float(vm[f]))
            points.append(p)
        self._write(points, "vm_telemetry")

    def write_vm_fs_telemetry(self, device_id: str, filesystems: List[Dict[str, Any]]) -> None:
        now = datetime.now(timezone.utc)
        points = [
            Point("vm_fs_telemetry").tag("device_id", device_id).tag("vm_name", fs["vm_name"])
            .tag("vcenter", fs.get("vcenter") or "").tag("cluster", fs.get("cluster") or "")
            .tag("mount_point", fs["mount_point"])
            .field("capacity_gb", float(fs["capacity_gb"])).field("free_gb", float(fs["free_gb"]))
            .field("used_gb", float(fs["used_gb"])).field("usage_pct", float(fs["usage_pct"]))
            .time(now, WritePrecision.S)
            for fs in filesystems
        ]
        self._write(points, "vm_fs_telemetry")

    def write_ds_telemetry(self, device_id: str, datastores: List[Dict[str, Any]]) -> None:
        now = datetime.now(timezone.utc)
        points = []
        for ds in datastores:
            p = (Point("ds_telemetry").tag("device_id", device_id).tag("datastore_name", ds["datastore_name"])
                 .time(now, WritePrecision.S))
            for f in DS_FIELDS:
                if isinstance(ds.get(f), (int, float)):
                    p.field(f, float(ds[f]))
            points.append(p)
        self._write(points, "ds_telemetry")

    def write_cluster_telemetry(self, device_id: str, clusters: List[Dict[str, Any]]) -> None:
        """Historique des clusters vu par vROps (tendances de l'onglet vRealize, Explorateur)."""
        now = datetime.now(timezone.utc)
        points = []
        for c in clusters:
            p = Point("cluster_telemetry").tag("device_id", device_id).tag("cluster_name", c["name"]).time(now, WritePrecision.S)
            fields = [f for f in CLUSTER_FIELDS if isinstance(c.get(f), (int, float))]
            for f in fields:
                p.field(f, float(c[f]))
            if fields:
                points.append(p)
        self._write(points, "cluster_telemetry")

    # ── Lecture : équipements ───────────────────────────────────────────────
    def get_metric_history(self, device_id: str, field_name: str, hours: int = 6, points: int = 48) -> List[Dict[str, Any]]:
        """Série d'une métrique d'équipement, agrégée pour tenir en ~`points` valeurs."""
        if field_name not in DEVICE_FIELDS:
            return []
        every = timedelta(seconds=max(60, int(hours * 3600 / points)))
        flux = '''
        from(bucket: params._bucket)
          |> range(start: params._start, stop: params._stop)
          |> filter(fn: (r) => r._measurement == "noc_telemetry" and r.device_id == params._device_id and r._field == params._field)
          |> aggregateWindow(every: params._every, fn: mean, createEmpty: false)
          |> keep(columns: ["_time", "_value"])
        '''
        tables = self._query(flux, {**_window(hours), "_device_id": device_id, "_field": field_name, "_every": every})
        return [
            {"timestamp": rec.get_time().isoformat(), "value": round(rec.get_value(), 2)}
            for t in tables for rec in t.records if rec.get_value() is not None
        ]

    # ── Lecture : VMs ───────────────────────────────────────────────────────
    def get_vcenters(self) -> List[str]:
        return self._tag_values("vcenter", "vm_telemetry")

    def get_clusters(self, vcenter: Optional[str]) -> List[str]:
        if not vcenter:
            return self._tag_values("cluster", "vm_telemetry")
        flux = '''
        import "influxdata/influxdb/schema"
        schema.tagValues(bucket: params._bucket, tag: "cluster",
          predicate: (r) => r._measurement == "vm_telemetry" and r.vcenter == params._vcenter, start: -30d)
        '''
        return sorted({rec.get_value() for t in self._query(flux, {"_vcenter": vcenter}) for rec in t.records if rec.get_value()})

    def _tag_values(self, tag: str, measurement: str) -> List[str]:
        flux = '''
        import "influxdata/influxdb/schema"
        schema.tagValues(bucket: params._bucket, tag: params._tag,
          predicate: (r) => r._measurement == params._measurement, start: -30d)
        '''
        tables = self._query(flux, {"_tag": tag, "_measurement": measurement})
        return sorted({rec.get_value() for t in tables for rec in t.records if rec.get_value()})

    def get_vm_saturation(self, start: datetime, end: datetime, threshold: float, duration_minutes: int,
                          vcenter: Optional[str], cluster: Optional[str], device_id: Optional[str]) -> List[Dict[str, Any]]:
        """VMs restées au-dessus de `threshold` % (CPU ou RAM) pendant au moins `duration_minutes`."""
        flux = '''
        from(bucket: params._bucket)
          |> range(start: params._start, stop: params._stop)
          |> filter(fn: (r) => r._measurement == "vm_telemetry")
          |> filter(fn: (r) => r._field == "cpu_usage_pct" or r._field == "ram_usage_pct")
          |> filter(fn: (r) => params._vcenter == "" or r.vcenter == params._vcenter)
          |> filter(fn: (r) => params._cluster == "" or r.cluster == params._cluster)
          |> filter(fn: (r) => params._device_id == "" or r.device_id == params._device_id)
          |> stateDuration(fn: (r) => r._value > params._threshold, column: "sat_s", unit: 1s)
          |> filter(fn: (r) => r.sat_s >= params._min_s)
          |> group(columns: ["vm_name", "vcenter", "cluster", "_field"])
          |> reduce(identity: {max_value: 0.0, sat_s: 0},
                    fn: (r, accumulator) => ({
                      max_value: if r._value > accumulator.max_value then r._value else accumulator.max_value,
                      sat_s: if r.sat_s > accumulator.sat_s then r.sat_s else accumulator.sat_s}))
        '''
        params = {
            **_window(start=start, end=end),
            "_vcenter": vcenter or "", "_cluster": cluster or "", "_device_id": device_id or "",
            "_threshold": float(threshold), "_min_s": int(duration_minutes) * 60,
        }
        rows = []
        for t in self._query(flux, params):
            for rec in t.records:
                rows.append({
                    "vm_name": rec.values.get("vm_name"),
                    "vcenter": rec.values.get("vcenter"),
                    "cluster": rec.values.get("cluster"),
                    "resource": "CPU" if rec.values.get("_field") == "cpu_usage_pct" else "RAM",
                    "max_value": round(float(rec.values.get("max_value", 0.0)), 1),
                    "duration_minutes": int(rec.values.get("sat_s", 0)) // 60,
                })
        rows.sort(key=lambda r: (r["duration_minutes"], r["max_value"]), reverse=True)
        return rows

    def get_vm_series(self, vm_name: str, field: str, start: datetime, end: datetime) -> List[Dict[str, Any]]:
        if field not in VM_FIELDS:
            return []
        span_h = max(1.0, (_utc(end) - _utc(start)).total_seconds() / 3600)
        every = timedelta(seconds=max(60, int(span_h * 3600 / 300)))
        flux = '''
        from(bucket: params._bucket)
          |> range(start: params._start, stop: params._stop)
          |> filter(fn: (r) => r._measurement == "vm_telemetry" and r.vm_name == params._vm and r._field == params._field)
          |> aggregateWindow(every: params._every, fn: max, createEmpty: false)
          |> keep(columns: ["_time", "_value"])
        '''
        tables = self._query(flux, {**_window(start=start, end=end), "_vm": vm_name, "_field": field, "_every": every})
        return [{"timestamp": r.get_time().isoformat(), "value": round(r.get_value(), 1)}
                for t in tables for r in t.records if r.get_value() is not None]

    def get_vm_filesystems(self, vcenter: Optional[str], cluster: Optional[str]) -> List[Dict[str, Any]]:
        flux = '''
        from(bucket: params._bucket)
          |> range(start: params._start, stop: params._stop)
          |> filter(fn: (r) => r._measurement == "vm_fs_telemetry")
          |> filter(fn: (r) => params._vcenter == "" or r.vcenter == params._vcenter)
          |> filter(fn: (r) => params._cluster == "" or r.cluster == params._cluster)
          |> last()
        '''
        fs: Dict[str, Dict[str, Any]] = {}
        for t in self._query(flux, {**_window(hours=1), "_vcenter": vcenter or "", "_cluster": cluster or ""}):
            for rec in t.records:
                key = f'{rec.values.get("vm_name")}|{rec.values.get("mount_point")}'
                row = fs.setdefault(key, {
                    "id": key, "vm_name": rec.values.get("vm_name"), "vcenter": rec.values.get("vcenter"),
                    "cluster": rec.values.get("cluster"), "mount_point": rec.values.get("mount_point"),
                    "capacity_gb": None, "free_gb": None, "used_gb": None, "usage_pct": None,
                })
                row[rec.get_field()] = round(rec.get_value(), 2)
        return sorted(fs.values(), key=lambda r: r["usage_pct"] or 0, reverse=True)

    def get_vms_latest(self, vm_names: Iterable[str]) -> Dict[str, Dict[str, float]]:
        """Dernières valeurs CPU / RAM / stockage pour une liste de VMs (15 dernières minutes)."""
        names = list(vm_names)
        if not names:
            return {}
        flux = '''
        from(bucket: params._bucket)
          |> range(start: params._start, stop: params._stop)
          |> filter(fn: (r) => r._measurement == "vm_telemetry")
          |> filter(fn: (r) => r._field == "cpu_usage_pct" or r._field == "ram_usage_pct" or r._field == "storage_gb")
          |> filter(fn: (r) => contains(value: r.vm_name, set: params._names))
          |> last()
        '''
        out: Dict[str, Dict[str, float]] = {}
        for t in self._query(flux, {**_window(hours=0.25), "_names": names}):
            for rec in t.records:
                out.setdefault(rec.values.get("vm_name"), {})[rec.get_field()] = rec.get_value()
        return out

    def get_vm_latency_history(self, hours: int, min_peak_ms: float) -> List[Dict[str, Any]]:
        """Historique de latence lecture/écriture des VMs ayant dépassé `min_peak_ms`."""
        every = timedelta(seconds=max(60, int(hours * 3600 / 120)))
        flux = '''
        from(bucket: params._bucket)
          |> range(start: params._start, stop: params._stop)
          |> filter(fn: (r) => r._measurement == "vm_telemetry")
          |> filter(fn: (r) => r._field == "read_latency_ms" or r._field == "write_latency_ms")
          |> aggregateWindow(every: params._every, fn: max, createEmpty: false)
          |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
          |> group(columns: ["vm_name", "device_id"])
        '''
        vms: Dict[str, Dict[str, Any]] = {}
        for t in self._query(flux, {**_window(hours), "_every": every}):
            for rec in t.records:
                name = rec.values.get("vm_name")
                if not name:
                    continue
                vm = vms.setdefault(name, {"vm_name": name, "device_id": rec.values.get("device_id"), "history": [], "peak_ms": 0.0})
                read = rec.values.get("read_latency_ms")
                write = rec.values.get("write_latency_ms")
                vm["history"].append({"timestamp": rec.get_time().isoformat(), "read_latency_ms": read, "write_latency_ms": write})
                vm["peak_ms"] = max(vm["peak_ms"], read or 0.0, write or 0.0)
        return sorted((v for v in vms.values() if v["peak_ms"] > min_peak_ms), key=lambda v: v["peak_ms"], reverse=True)

    # ── Explorateur de métriques ────────────────────────────────────────────
    def get_objects_list(self) -> Dict[str, List[str]]:
        return {
            "Virtual Machine": self._tag_values("vm_name", "vm_telemetry"),
            "Datastore": self._tag_values("datastore_name", "ds_telemetry"),
            "Cluster": self._tag_values("cluster_name", "cluster_telemetry"),
        }

    def get_object_history(self, object_type: str, object_name: str, field: str, hours: int) -> List[Dict[str, Any]]:
        if object_type not in OBJECT_TYPES:
            return []
        measurement, tag, fields = OBJECT_TYPES[object_type]
        if field not in fields:
            return []
        every = timedelta(seconds=max(60, int(hours * 3600 / 300)))
        # `tag` provient de la liste blanche OBJECT_TYPES (constante), seule la valeur est paramétrée.
        flux = f'''
        from(bucket: params._bucket)
          |> range(start: params._start, stop: params._stop)
          |> filter(fn: (r) => r._measurement == params._measurement and r._field == params._field)
          |> filter(fn: (r) => r.{tag} == params._name)
          |> aggregateWindow(every: params._every, fn: mean, createEmpty: false)
          |> keep(columns: ["_time", "_value"])
        '''
        tables = self._query(flux, {**_window(hours), "_measurement": measurement,
                                    "_name": object_name, "_field": field, "_every": every})
        return [{"timestamp": r.get_time().isoformat(), "value": round(r.get_value(), 2)}
                for t in tables for r in t.records if r.get_value() is not None]


_manager: Optional[InfluxDBManager] = None


def get_influx() -> InfluxDBManager:
    """Instance unique, créée à la première utilisation (pas à l'import)."""
    global _manager
    if _manager is None:
        _manager = InfluxDBManager()
    return _manager
