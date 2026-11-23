"""
État temps réel des équipements.

L'API ne contacte JAMAIS les équipements : elle lit le dernier état déposé dans Redis
par les workers. Un équipement sans donnée est affiché « NO_DATA », une donnée trop
ancienne est marquée `stale` — aucune valeur n'est inventée.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.influx import DEVICE_FIELDS, OBJECT_TYPES, get_influx
from app.core.redis_client import LATEST_KEY, r_client
from app.models.inventory import InfrastructureDevice
from app.services.collectors import effective_interval
from app.services.settings_service import get_polling_interval

logger = logging.getLogger(__name__)
router = APIRouter()

# Métrique utilisée pour la mini-courbe de chaque catégorie.
SPARKLINE_FIELD = {
    "VIRTUALIZATION": "cpu_usage_pct",
    "STORAGE": "iops",
    "BACKUP": "capacity_usage_pct",
    "OBJECT": "capacity_usage_pct",
}
SPARKLINE_FIELD_BY_TYPE = {"NETBACKUP": "backup_success_rate", "VERITAS_ACCESS": "capacity_usage_pct",
                           "VROPS": "alerts_critical"}

# Détails volumineux exclus de la vue d'ensemble (disponibles via /devices/{id}).
_HEAVY_KEYS = ("vms_metrics", "vms_fs_metrics", "hosts_metrics", "datastores_metrics",
               "clusters_metrics", "vrops_datastores", "rightsizing", "vrops_hosts", "vm_contention",
               "snapshots", "vrops_alerts", "health_summary", "vrops_missing_keys", "vrops_diagnostics",
               "storage_pools")


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _load_states(devices: List[InfrastructureDevice]) -> Dict[str, Optional[dict]]:
    if not devices:
        return {}
    raw = r_client.mget([LATEST_KEY.format(device_id=d.id) for d in devices])
    out: Dict[str, Optional[dict]] = {}
    for dev, blob in zip(devices, raw):
        try:
            out[dev.id] = json.loads(blob) if blob else None
        except (ValueError, TypeError):
            out[dev.id] = None
    return out


def _device_view(dev: InfrastructureDevice, payload: Optional[dict], interval_s: int, full: bool) -> Dict[str, Any]:
    collected_at = _parse_ts(payload.get("collected_at") or payload.get("timestamp")) if payload else None
    age_s = (datetime.now(timezone.utc) - collected_at).total_seconds() if collected_at else None
    view: Dict[str, Any] = {
        "device_id": dev.id,
        "device_name": dev.display_name,
        "device_type": dev.device_type,
        "category": dev.category,
        "hostname_ip": dev.hostname_ip,
        "is_active": dev.is_active,
        "last_sync_status": dev.last_sync_status,
        "last_sync_error": dev.last_sync_error,
        "collected_at": collected_at.isoformat() if collected_at else None,
        "stale": age_s is None or age_s > effective_interval(dev.device_type, interval_s) * 3,  # 3 cycles manqués
        "status": (payload or {}).get("status", "NO_DATA") if payload else "NO_DATA",
        "metrics": (payload or {}).get("metrics", {}),
        "alerts": (payload or {}).get("alerts", []),
    }
    if full and payload:
        for key in _HEAVY_KEYS:
            if key in ("vms_metrics", "vms_fs_metrics"):
                continue  # trop volumineux : servis par les écrans VMs dédiés
            if key in payload:
                view[key] = payload[key]
    return view


@router.get("/realtime")
def get_realtime_metrics(
    with_sparkline: bool = Query(True, description="Ajouter la mini-courbe des 6 dernières heures"),
    db: Session = Depends(get_db),
):
    """Dernier état connu de chaque équipement non archivé."""
    devices = (
        db.query(InfrastructureDevice)
        .filter(InfrastructureDevice.is_archived.is_(False))
        .order_by(InfrastructureDevice.category, InfrastructureDevice.display_name)
        .all()
    )
    interval = get_polling_interval(db)
    try:
        states = _load_states(devices)
    except Exception as exc:
        logger.error("Redis indisponible : %s", exc)
        raise HTTPException(status_code=503, detail="Cache temps réel indisponible.")

    influx = get_influx() if with_sparkline else None
    result = []
    for dev in devices:
        view = _device_view(dev, states.get(dev.id), interval, full=False)
        if influx is not None:
            field = SPARKLINE_FIELD_BY_TYPE.get(dev.device_type) or SPARKLINE_FIELD.get(dev.category, "cpu_usage_pct")
            view["sparkline"] = {"field": field, "points": influx.get_metric_history(dev.id, field, hours=6, points=36)}
        result.append(view)
    return result


@router.get("/devices/{device_id}")
def get_device_detail(device_id: str, db: Session = Depends(get_db)):
    """État détaillé d'un équipement (hôtes ESXi, datastores, alertes)."""
    dev = db.get(InfrastructureDevice, device_id)
    if not dev or dev.is_archived:
        raise HTTPException(status_code=404, detail="Équipement introuvable.")
    blob = r_client.get(LATEST_KEY.format(device_id=dev.id))
    payload = json.loads(blob) if blob else None
    return _device_view(dev, payload, get_polling_interval(db), full=True)


@router.get("/devices/{device_id}/history")
def get_device_history(
    device_id: str,
    field: str = Query(..., description="Nom de la métrique"),
    hours: int = Query(24, ge=1, le=24 * 30),
    db: Session = Depends(get_db),
):
    if field not in DEVICE_FIELDS:
        raise HTTPException(status_code=400, detail="Métrique inconnue.")
    if not db.get(InfrastructureDevice, device_id):
        raise HTTPException(status_code=404, detail="Équipement introuvable.")
    return {"field": field, "points": get_influx().get_metric_history(device_id, field, hours=hours, points=300)}


# ── Explorateur de métriques (VMs, datastores) ──────────────────────────────
@router.get("/explorer/objects")
def get_explorer_objects():
    """Objets ayant remonté des métriques sur les 30 derniers jours, et champs disponibles."""
    influx = get_influx()
    return {
        "objects": influx.get_objects_list(),
        "fields": {name: sorted(fields) for name, (_, _, fields) in OBJECT_TYPES.items()},
    }


@router.get("/explorer/history")
def get_explorer_history(
    object_type: str = Query(...),
    object_name: str = Query(..., max_length=256),
    metric_field: str = Query(...),
    hours: int = Query(12, ge=1, le=24 * 30),
):
    if object_type not in OBJECT_TYPES or metric_field not in OBJECT_TYPES[object_type][2]:
        raise HTTPException(status_code=400, detail="Type d'objet ou métrique inconnu.")
    return {
        "object_type": object_type,
        "object_name": object_name,
        "metric_field": metric_field,
        "history": get_influx().get_object_history(object_type, object_name, metric_field, hours),
    }
