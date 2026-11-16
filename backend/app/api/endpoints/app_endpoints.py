import logging
import random
from typing import List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.application import Application, VM
from app.schemas.application import ApplicationCreate, ApplicationResponse, VMSchema
from app.core.influx import influx_manager, INFLUXDB_BUCKET, INFLUXDB_ORG

logger = logging.getLogger(__name__)
router = APIRouter()

@router.get("/vms/list", response_model=List[VMSchema])
def list_available_vms(db: Session = Depends(get_db)):
    """
    Retourne la liste complète des VMs disponibles en base (pour cocher les VMs dans l'UI).
    """
    vms = db.query(VM).order_by(VM.name).all()
    return vms

@router.get("", response_model=List[ApplicationResponse])
def get_all_applications(db: Session = Depends(get_db)):
    """
    Liste toutes les applications créées.
    """
    apps = db.query(Application).all()
    return apps

@router.get("/{app_id}", response_model=ApplicationResponse)
def get_application_by_id(app_id: int, db: Session = Depends(get_db)):
    """
    Récupère une application spécifique par son ID.
    """
    app = db.query(Application).filter(Application.id == app_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="Application non trouvée")
    return app

@router.post("", response_model=ApplicationResponse, status_code=status.HTTP_201_CREATED)
def create_application(payload: ApplicationCreate, db: Session = Depends(get_db)):
    """
    Crée une nouvelle application et l'associe à la liste des IDs de VMs fournies.
    """
    # Vérifier l'unicité du nom
    existing_app = db.query(Application).filter(Application.name == payload.name).first()
    if existing_app:
        raise HTTPException(status_code=400, detail="Une application avec ce nom existe déjà")

    # Récupérer les VMs cibles
    vms = db.query(VM).filter(VM.id.in_(payload.vm_ids)).all()
    if len(vms) != len(payload.vm_ids):
        logger.warning(f"Certains IDs de VMs n'ont pas été trouvés sur les {payload.vm_ids}")

    # Créer l'application
    new_app = Application(
        name=payload.name,
        description=payload.description,
        vms=vms
    )
    
    db.add(new_app)
    try:
        db.commit()
        db.refresh(new_app)
        logger.info(f"Application '{new_app.name}' créée avec succès (VMs associées: {len(vms)})")
        return new_app
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur d'écriture de l'application : {str(e)}")
        raise HTTPException(status_code=500, detail="Impossible de créer l'application")

@router.delete("/{app_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_application(app_id: int, db: Session = Depends(get_db)):
    """
    Supprime une application par son ID. Les VMs associées ne sont pas supprimées.
    """
    app = db.query(Application).filter(Application.id == app_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="Application non trouvée")
        
    db.delete(app)
    try:
        db.commit()
        logger.info(f"Application ID {app_id} supprimée avec succès")
        return
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur lors de la suppression de l'application {app_id} : {str(e)}")
        raise HTTPException(status_code=500, detail="Impossible de supprimer l'application")

@router.put("/{app_id}", response_model=ApplicationResponse)
def update_application(app_id: int, payload: ApplicationCreate, db: Session = Depends(get_db)):
    """
    Met à jour une application existante (nom, description, et liste de VMs associées).
    """
    app = db.query(Application).filter(Application.id == app_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="Application non trouvée")

    # Vérifier l'unicité du nom si on le change
    if payload.name != app.name:
        existing_app = db.query(Application).filter(Application.name == payload.name).first()
        if existing_app:
            raise HTTPException(status_code=400, detail="Une autre application avec ce nom existe déjà")

    # Récupérer les VMs cibles
    vms = db.query(VM).filter(VM.id.in_(payload.vm_ids)).all()
    if len(vms) != len(payload.vm_ids):
        logger.warning(f"Certains IDs de VMs n'ont pas été trouvés lors de la mise à jour de l'app {app_id}")

    app.name = payload.name
    app.description = payload.description
    app.vms = vms
    
    try:
        db.commit()
        db.refresh(app)
        logger.info(f"Application '{app.name}' mise à jour avec succès (VMs associées: {len(vms)})")
        return app
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur lors de la mise à jour de l'application {app_id} : {str(e)}")
        raise HTTPException(status_code=500, detail="Impossible de mettre à jour l'application")

@router.get("/{app_id}/metrics")
def get_application_metrics(app_id: int, db: Session = Depends(get_db)):
    """
    Agrège les performances en temps réel de toutes les VMs de l'application.
    Retourne la vue globale (moyenne CPU/RAM, somme stockage) et la vue granulaire de chaque VM.
    """
    app = db.query(Application).filter(Application.id == app_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="Application non trouvée")
        
    vms = app.vms
    if not vms:
        return {
            "summary": {
                "avg_cpu": 0.0,
                "avg_ram": 0.0,
                "total_storage_tb": 0.0,
                "vms_active": 0,
                "vms_total": 0
            },
            "vms": []
        }
        
    vm_details = []
    total_cpu = 0.0
    total_ram = 0.0
    total_storage_gb = 0.0
    vms_active = 0
    
    vm_names = [vm.name for vm in vms]
    real_metrics = {}
    
    if influx_manager.query_api and vm_names:
        try:
            names_filter = " or ".join([f'r["vm_name"] == "{name}"' for name in vm_names])
            query = f'''
            from(bucket: "{INFLUXDB_BUCKET}")
              |> range(start: -15m)
              |> filter(fn: (r) => r["_measurement"] == "vm_telemetry")
              |> filter(fn: (r) => r["_field"] == "cpu_usage_pct" or r["_field"] == "ram_usage_pct" or r["_field"] == "storage_gb")
              |> filter(fn: (r) => {names_filter})
              |> last()
            '''
            result = influx_manager.query_api.query(org=INFLUXDB_ORG, query=query)
            for table in result:
                for record in table.records:
                    v_name = record.values.get("vm_name")
                    field = record.get_field()
                    val = record.get_value()
                    if v_name not in real_metrics:
                        real_metrics[v_name] = {"cpu_usage_pct": 0.0, "ram_usage_pct": 0.0}
                    real_metrics[v_name][field] = val
        except Exception as e:
            logger.error(f"Error fetching app metrics from influx: {e}")

    for vm in vms:
        # Initialiser avec des valeurs par défaut
        storage = 0.0
        
        vm_data = real_metrics.get(vm.name)
        if vm_data:
            cpu = round(vm_data.get("cpu_usage_pct", 0.0), 1)
            ram = round(vm_data.get("ram_usage_pct", 0.0), 1)
            storage = round(vm_data.get("storage_gb", 0.0), 1)
            
            # Si le stockage est manquant dans Influx (ancienne donnée), on assigne 0.0
            if storage == 0.0:
                storage = 0.0
                
            vm_status = "Online"
            total_cpu += cpu
            total_ram += ram
            vms_active += 1
        else:
            cpu = 0.0
            ram = 0.0
            vm_status = "Offline"
            
        total_storage_gb += storage
        
        vm_details.append({
            "id": vm.id,
            "name": vm.name,
            "vcenter": vm.vcenter,
            "cluster": vm.cluster,
            "ip": vm.ip,
            "os": vm.os,
            "cpu_usage_pct": cpu,
            "ram_usage_pct": ram,
            "storage_used_gb": storage,
            "status": vm_status
        })
        
    vms_total = len(vms)
    avg_cpu = round(total_cpu / vms_active, 1) if vms_active > 0 else 0.0
    avg_ram = round(total_ram / vms_active, 1) if vms_active > 0 else 0.0
    total_storage_tb = round(total_storage_gb / 1024.0, 2)
    
    return {
        "summary": {
            "avg_cpu": avg_cpu,
            "avg_ram": avg_ram,
            "total_storage_tb": total_storage_tb,
            "vms_active": vms_active,
            "vms_total": vms_total
        },
        "vms": vm_details
    }

def datetime_seed_offset() -> int:
    """
    Retourne un décalage basé sur l'heure actuelle (arrondi à la minute)
    pour que les fluctuations de métriques changent toutes les minutes.
    """
    from datetime import datetime
    now = datetime.utcnow()
    return now.minute + now.hour * 60
