"""Diagnostic des VMs : saturation CPU/RAM, historique, filesystems invités, latence."""
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.influx import get_influx
from app.models.application import VM

router = APIRouter()

MAX_RANGE = timedelta(days=31)


def _range(start: Optional[datetime], end: Optional[datetime]) -> tuple[datetime, datetime]:
    end = end or datetime.now(timezone.utc)
    start = start or end - timedelta(days=1)
    if start >= end:
        raise HTTPException(status_code=400, detail="La date de début doit précéder la date de fin.")
    if end - start > MAX_RANGE:
        raise HTTPException(status_code=400, detail="La période ne peut pas dépasser 31 jours.")
    return start, end


def _clean(value: Optional[str]) -> Optional[str]:
    return None if not value or value == "all" else value


@router.get("/filters/vcenters")
def list_vcenters() -> List[str]:
    return get_influx().get_vcenters()


@router.get("/filters/clusters")
def list_clusters(vcenter: Optional[str] = Query(None)) -> List[str]:
    return get_influx().get_clusters(_clean(vcenter))


@router.get("/saturation")
def get_vm_saturation(
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    vcenter: Optional[str] = Query(None),
    cluster: Optional[str] = Query(None),
    threshold: float = Query(90.0, ge=1, le=100),
    duration_minutes: int = Query(15, ge=1, le=24 * 60),
    device_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """VMs restées au-dessus du seuil pendant au moins `duration_minutes`.
    OS et IP proviennent de l'inventaire des VMs ; absents s'ils ne sont pas connus."""
    start, end = _range(start_date, end_date)
    rows = get_influx().get_vm_saturation(start, end, threshold, duration_minutes,
                                          _clean(vcenter), _clean(cluster), _clean(device_id))
    names = {r["vm_name"] for r in rows if r["vm_name"]}
    inventory: Dict[str, VM] = {vm.name: vm for vm in db.query(VM).filter(VM.name.in_(names)).all()} if names else {}
    for r in rows:
        vm = inventory.get(r["vm_name"])
        r["os"] = vm.os if vm and vm.os else None
        r["ip"] = vm.ip if vm and vm.ip else None
    return rows


@router.get("/saturation/{vm_name}/history")
def get_vm_saturation_history(
    vm_name: str,
    resource: str = Query("CPU", pattern="^(CPU|RAM)$"),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
):
    start, end = _range(start_date, end_date)
    field = "cpu_usage_pct" if resource == "CPU" else "ram_usage_pct"
    return get_influx().get_vm_series(vm_name, field, start, end)


@router.get("/filesystems")
def get_vm_filesystems(vcenter: Optional[str] = Query(None), cluster: Optional[str] = Query(None)):
    return get_influx().get_vm_filesystems(_clean(vcenter), _clean(cluster))


@router.get("/latency")
def get_vm_latency(
    hours: int = Query(6, ge=1, le=72),
    min_peak_ms: float = Query(10.0, ge=0),
):
    """Historique de latence disque des VMs dont le pic dépasse `min_peak_ms`."""
    return get_influx().get_vm_latency_history(hours=hours, min_peak_ms=min_peak_ms)
