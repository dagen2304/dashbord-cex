"""Centre d'alertes : alertes en cours (dernier état des collecteurs) et historique de latence."""
import json
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.redis_client import LATEST_KEY, r_client
from app.core.security import audit, require_operator
from app.models.inventory import InfrastructureDevice, LatencyAnomaly
from app.models.user import User

router = APIRouter()

_SEVERITY_RANK = {"CRITICAL": 0, "WARNING": 1, "INFO": 2}


class LatencyAnomalyOut(BaseModel):
    id: int
    device_id: Optional[str]
    device_name: Optional[str] = None
    target_name: str
    latency_ms: float
    threshold_ms: float
    timestamp: datetime
    is_resolved: bool

    model_config = ConfigDict(from_attributes=True)


@router.get("/active")
def get_active_alerts(db: Session = Depends(get_db)):
    """Alertes remontées par la dernière collecte de chaque équipement actif."""
    devices = db.query(InfrastructureDevice).filter(
        InfrastructureDevice.is_archived.is_(False), InfrastructureDevice.is_active.is_(True)
    ).all()
    if not devices:
        return []
    blobs = r_client.mget([LATEST_KEY.format(device_id=d.id) for d in devices])
    alerts = []
    for dev, blob in zip(devices, blobs):
        if not blob:
            continue
        try:
            payload = json.loads(blob)
        except ValueError:
            continue
        for a in payload.get("alerts", []):
            alerts.append({
                "device_id": dev.id,
                "device_name": dev.display_name,
                "device_type": dev.device_type,
                "severity": a.get("severity", "WARNING"),
                "component": a.get("component"),
                "message": a.get("message", ""),
                "collected_at": payload.get("collected_at") or payload.get("timestamp"),
            })
    alerts.sort(key=lambda a: (_SEVERITY_RANK.get(a["severity"], 9), a["device_name"]))
    return alerts


@router.get("/latency", response_model=List[LatencyAnomalyOut])
def get_latency_anomalies(
    include_resolved: bool = Query(False),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    q = db.query(LatencyAnomaly, InfrastructureDevice.display_name).outerjoin(
        InfrastructureDevice, InfrastructureDevice.id == LatencyAnomaly.device_id
    )
    if not include_resolved:
        q = q.filter(LatencyAnomaly.is_resolved.is_(False))
    rows = q.order_by(LatencyAnomaly.timestamp.desc()).limit(limit).all()
    out = []
    for anomaly, device_name in rows:
        item = LatencyAnomalyOut.model_validate(anomaly)
        item.device_name = device_name
        out.append(item)
    return out


@router.post("/latency/{anomaly_id}/resolve", response_model=LatencyAnomalyOut)
def resolve_latency_anomaly(anomaly_id: int, user: User = Depends(require_operator), db: Session = Depends(get_db)):
    anomaly = db.get(LatencyAnomaly, anomaly_id)
    if not anomaly:
        raise HTTPException(status_code=404, detail="Alerte introuvable.")
    anomaly.is_resolved = True
    audit(db, user, "alert.resolved", anomaly.target_name, {"id": anomaly_id})
    db.commit()
    db.refresh(anomaly)
    return anomaly
