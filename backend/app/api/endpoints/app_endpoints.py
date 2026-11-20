"""Applications métier : regroupement de VMs et agrégation de leurs métriques."""
import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.influx import get_influx
from app.core.security import audit, require_operator
from app.models.application import VM, Application
from app.models.user import User
from app.schemas.application import ApplicationCreate, ApplicationResponse, VMSchema

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/vms/list", response_model=List[VMSchema])
def list_available_vms(
    search: str = Query("", max_length=128),
    limit: int = Query(500, ge=1, le=5000),
    db: Session = Depends(get_db),
):
    q = db.query(VM)
    if search:
        q = q.filter(VM.name.ilike(f"%{search}%"))
    return q.order_by(VM.name).limit(limit).all()


@router.get("", response_model=List[ApplicationResponse])
def get_all_applications(db: Session = Depends(get_db)):
    return db.query(Application).order_by(Application.name).all()


@router.get("/{app_id}", response_model=ApplicationResponse)
def get_application_by_id(app_id: int, db: Session = Depends(get_db)):
    app = db.get(Application, app_id)
    if not app:
        raise HTTPException(status_code=404, detail="Application non trouvée")
    return app


def _resolve_vms(db: Session, vm_ids: List[int]) -> List[VM]:
    ids = set(vm_ids)
    vms = db.query(VM).filter(VM.id.in_(ids)).all() if ids else []
    if len(vms) != len(ids):
        missing = ids - {vm.id for vm in vms}
        raise HTTPException(status_code=400, detail=f"VMs introuvables : {sorted(missing)}")
    return vms


@router.post("", response_model=ApplicationResponse, status_code=status.HTTP_201_CREATED)
def create_application(payload: ApplicationCreate, user: User = Depends(require_operator), db: Session = Depends(get_db)):
    if db.query(Application).filter(Application.name == payload.name).first():
        raise HTTPException(status_code=400, detail="Une application avec ce nom existe déjà")
    app = Application(name=payload.name, description=payload.description, vms=_resolve_vms(db, payload.vm_ids))
    db.add(app)
    audit(db, user, "application.created", payload.name, {"vms": len(payload.vm_ids)})
    db.commit()
    db.refresh(app)
    return app


@router.put("/{app_id}", response_model=ApplicationResponse)
def update_application(app_id: int, payload: ApplicationCreate, user: User = Depends(require_operator),
                       db: Session = Depends(get_db)):
    app = db.get(Application, app_id)
    if not app:
        raise HTTPException(status_code=404, detail="Application non trouvée")
    if payload.name != app.name and db.query(Application).filter(Application.name == payload.name).first():
        raise HTTPException(status_code=400, detail="Une autre application avec ce nom existe déjà")
    app.name = payload.name
    app.description = payload.description
    app.vms = _resolve_vms(db, payload.vm_ids)
    audit(db, user, "application.updated", payload.name, {"vms": len(payload.vm_ids)})
    db.commit()
    db.refresh(app)
    return app


@router.delete("/{app_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_application(app_id: int, user: User = Depends(require_operator), db: Session = Depends(get_db)):
    app = db.get(Application, app_id)
    if not app:
        raise HTTPException(status_code=404, detail="Application non trouvée")
    audit(db, user, "application.deleted", app.name)
    db.delete(app)
    db.commit()


@router.get("/{app_id}/metrics")
def get_application_metrics(app_id: int, db: Session = Depends(get_db)):
    """Dernières métriques des VMs de l'application. Une VM sans donnée récente est « NO_DATA »
    et n'entre pas dans les moyennes."""
    app = db.get(Application, app_id)
    if not app:
        raise HTTPException(status_code=404, detail="Application non trouvée")

    latest = get_influx().get_vms_latest(vm.name for vm in app.vms)
    details, cpu, ram, storage_gb = [], [], [], 0.0
    for vm in app.vms:
        m = latest.get(vm.name)
        row = {"id": vm.id, "name": vm.name, "vcenter": vm.vcenter, "cluster": vm.cluster, "ip": vm.ip, "os": vm.os,
               "cpu_usage_pct": None, "ram_usage_pct": None, "storage_used_gb": None, "status": "NO_DATA"}
        if m:
            row.update(status="ONLINE",
                       cpu_usage_pct=round(m["cpu_usage_pct"], 1) if "cpu_usage_pct" in m else None,
                       ram_usage_pct=round(m["ram_usage_pct"], 1) if "ram_usage_pct" in m else None,
                       storage_used_gb=round(m["storage_gb"], 1) if "storage_gb" in m else None)
            if row["cpu_usage_pct"] is not None:
                cpu.append(row["cpu_usage_pct"])
            if row["ram_usage_pct"] is not None:
                ram.append(row["ram_usage_pct"])
            storage_gb += row["storage_used_gb"] or 0.0
        details.append(row)

    return {
        "summary": {
            "avg_cpu": round(sum(cpu) / len(cpu), 1) if cpu else None,
            "avg_ram": round(sum(ram) / len(ram), 1) if ram else None,
            "total_storage_tb": round(storage_gb / 1024, 2),
            "vms_active": sum(1 for d in details if d["status"] == "ONLINE"),
            "vms_total": len(details),
        },
        "vms": details,
    }
