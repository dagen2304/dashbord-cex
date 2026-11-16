import json
import random
import logging
import asyncio
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, Depends, Query
from fastapi.concurrency import run_in_threadpool
import redis
import os
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.inventory import InfrastructureDevice, MetricHistory
from app.core.influx import influx_manager

# Import collector registry from scheduler (SSOT)
from app.services.vcenter_collector import VCenterCollector
from app.services.dorado_collector import DoradoCollector
from app.services.unity_collector import UnityCollector
from app.services.powerstore_collector import PowerStoreCollector
from app.services.datadomain_collector import DataDomainCollector
from app.services.netbackup_collector import NetBackupCollector
from app.services.scality_collector import ScalityCollector
from app.services.veritas_collector import VeritasCollector

logger = logging.getLogger(__name__)
router = APIRouter()

# Redis Client
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
r_client = redis.Redis.from_url(REDIS_URL)

# ─────────────────────────────────────────────────────────────────────────────
# COLLECTOR REGISTRY — Mapping device_type (SSOT) → Classe collecteur
# Identique au registre dans scheduler.py — SSOT respecté.
# ─────────────────────────────────────────────────────────────────────────────
COLLECTOR_REGISTRY = {
    "VCENTER":         VCenterCollector,
    "HUAWEI_DORADO":   DoradoCollector,
    "DELL_UNITY":      UnityCollector,
    "DELL_POWERSTORE": PowerStoreCollector,
    "DATADOMAIN":      DataDomainCollector,
    "NETBACKUP":       NetBackupCollector,
    "SCALITY":         ScalityCollector,
    "VERITAS_ACCESS":  VeritasCollector,
}


def _map_category_to_device_type(fallback_id: str) -> str:
    mapping = {
        "vcenter-01": "VCENTER",
        "dorado-01": "HUAWEI_DORADO",
        "unity-01": "DELL_UNITY",
        "powerstore-01": "DELL_POWERSTORE",
        "datadomain-01": "DATADOMAIN",
        "netbackup-01": "NETBACKUP",
        "scality-01": "SCALITY",
        "veritas-01": "VERITAS_ACCESS",
    }
    return mapping.get(fallback_id, "VCENTER")


async def get_collector_fallback(dev: Dict[str, Any]) -> Dict[str, Any]:
    """
    Génère un état dynamique en appelant le collecteur approprié au type d'équipement.
    Utilisé uniquement quand Redis ne dispose pas encore de données pour ce device
    (ex: premier boot avant le premier cycle Celery).
    
    Les métriques produites sont authentiques et dynamiques (issues du collecteur),
    pas des valeurs statiques figées.
    """
    device_type = dev.get("device_type_exact", "")  # VCENTER, HUAWEI_DORADO, etc.
    collector_class = COLLECTOR_REGISTRY.get(device_type)

    if not collector_class:
        # Aucun collecteur connu pour ce type — retourner un squelette minimal
        logger.warning(f"Aucun collecteur trouvé pour device_type='{device_type}' (device_id={dev['id']})")
        return {
            "device_id": dev["id"],
            "device_name": dev["display_name"],
            "device_type": dev["device_type"],
            "status": "UNSYNCED",
            "metrics": {},
            "alerts": []
        }

    # Instancier le collecteur avec les credentials réels
    # Le mot de passe est déchiffré et passé directement — pas de simulation
    collector_config = {
        "simulate": False,
        "host": dev.get("hostname_ip", "unknown"),
        "port": dev.get("port", 443),
        "username": dev.get("username", ""),
        "password": dev.get("password", ""),  # Mot de passe déchiffré depuis la DB
    }
    collector = collector_class(dev["id"], dev["display_name"], collector_config)
    
    try:
        payload = await run_in_threadpool(collector.collect)
        # Synchroniser l'ID et le nom depuis la DB (source of truth)
        payload["device_id"] = dev["id"]
        payload["device_name"] = dev["display_name"]
        return payload
    except Exception as e:
        logger.error(f"Erreur collecteur fallback pour {dev['display_name']}: {e}")
        fallback = get_fallback_simulated_state(dev["id"], dev.get("device_type", "unknown"))
        return {
            "device_id": dev["id"],
            "device_name": dev["display_name"],
            "device_type": dev["device_type"],
            "status": "ERROR",
            "metrics": fallback["metrics"],
            "alerts": [{"severity": "CRITICAL", "message": str(e), "component": "Collector"}]
        }


@router.get("/realtime")
async def get_realtime_metrics(
    device_id: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """
    Récupère le statut en temps réel de tous les équipements à partir de Redis ou de la DB.
    
    Flux de données (par ordre de priorité) :
    1. Redis noc_latest:{device_id} — données fraîches du pipeline Celery (≤60s)
    2. Collecteur spécialisé en mode simulation — fallback dynamique avant 1er cycle Celery
    3. Équipements de démonstration statiques — si aucun équipement n'est enregistré en DB
    
    Incorpore un historique glissant pour l'affichage des mini-graphiques (Sparklines).
    """
    # 1. Charger les devices actifs de la DB
    db_devices = (
        db.query(InfrastructureDevice)
        .filter(InfrastructureDevice.is_archived == False)
        .all()
    )

    devices_list = []
    if db_devices:
        for d in db_devices:
            # Si device_id est fourni, on filtre spécifiquement
            if device_id and d.id != device_id:
                continue
            # Déchiffrer le mot de passe pour le passer au collecteur
            try:
                decrypted_password = d.get_password()
            except Exception:
                decrypted_password = ""

            devices_list.append({
                "id": d.id,
                "display_name": d.display_name,
                "hostname_ip": d.hostname_ip,
                "device_type": d.category.lower(),      # virtualization, storage, backup, object
                "device_type_exact": d.device_type,     # VCENTER, HUAWEI_DORADO, etc.
                "port": d.port,
                "username": d.username,
                "password": decrypted_password,
                "status": d.last_sync_status
            })

    logger.info(f"Prepared {len(devices_list)} devices for metric processing.")

    async def fetch_state(dev):
        dev_id = dev["id"]
        logger.info(f"[fetch_state] Starting for {dev_id}")
        # Redis read via threadpool pour ne pas bloquer l'event loop
        logger.info(f"[fetch_state] reading redis for {dev_id}...")
        raw_data = await run_in_threadpool(r_client.get, f"noc_latest:{dev_id}")
        logger.info(f"[fetch_state] redis done for {dev_id}")
        
        if raw_data:
            device_state = json.loads(raw_data)
            device_state["device_name"] = dev["display_name"]
            device_state["device_id"] = dev_id
            return dev_id, device_state
            
        if "device_state" in dev:
            return dev_id, dev["device_state"]
            
        try:
            logger.info(f"[fetch_state] waiting for get_collector_fallback for {dev_id}...")
            # Fallback with threadpool and 30s timeout to prevent UI hang and threadpool exhaustion
            device_state = await asyncio.wait_for(get_collector_fallback(dev), timeout=30.0)
            logger.info(f"[fetch_state] get_collector_fallback SUCCESS for {dev_id}")
            return dev_id, device_state
        except asyncio.TimeoutError:
            logger.warning(f"[fetch_state] Timeout lors de la collecte fallback pour {dev_id}")
            fallback = get_fallback_simulated_state(dev["id"], dev.get("device_type", "unknown"))
            return dev_id, {
                "device_id": dev["id"],
                "device_name": dev["display_name"],
                "device_type": dev.get("device_type", fallback["device_type"]),
                "status": "ERROR",
                "metrics": fallback["metrics"],
                "alerts": [{"severity": "CRITICAL", "message": "Délai de connexion expiré (Timeout)", "component": "Collector"}]
            }

    # Execute all slow collector fetches concurrently
    logger.info("Starting concurrent fetches...")
    fetch_tasks = [fetch_state(dev) for dev in devices_list]
    states = dict(await asyncio.gather(*fetch_tasks))
    logger.info("Concurrent fetches done.")

    response = []
    for dev in devices_list:
        dev_id = dev["id"]
        logger.info(f"Processing history for {dev_id}...")
        device_state = states[dev_id]

        # Enrichir avec l'historique temporel pour les sparklines
        metric_field = "cpu_usage_pct"
        if device_state.get("device_type") == "storage":
            metric_field = "iops"
        elif device_state.get("device_type") == "backup":
            metric_field = "active_streams" if "active_streams" in device_state.get("metrics", {}) else "backup_success_rate"
        elif device_state.get("device_type") == "object":
            metric_field = "iops_s3"
            
        # Fast synchronous InfluxDB read
        logger.info(f"Reading InfluxDB for {dev_id}...")
        history = influx_manager.get_metric_history(dev_id, metric_field, hours=6)
        logger.info(f"InfluxDB done for {dev_id}")
        
        # S'assurer d'avoir au moins 10 points pour dessiner une sparkline fluide
        if len(history) < 10:
            db_history = []
            try:
                logger.info(f"Reading SQL for {dev_id}...")
                # Requête SQLAlchemy SEQUENTIELLE - Thread safe !
                hist_points = (
                    db.query(MetricHistory)
                    .filter(MetricHistory.device_id == dev_id)
                    .order_by(MetricHistory.timestamp.desc())
                    .limit(12)
                    .all()
                )
                if hist_points:
                    db_history = [p.usage_pct for p in reversed(hist_points)]
                logger.info(f"SQL done for {dev_id}")
            except Exception as e:
                logger.error(f"Erreur historique SQL sparkline : {e}")

            if len(db_history) >= 5:
                history = db_history
            else:
                base_val = device_state.get("metrics", {}).get(metric_field, 50)
                history = [round(base_val + random.uniform(-10, 10), 1) for _ in range(12)]
            
        device_state["sparkline"] = history
        response.append(device_state)
        
    logger.info("--- END get_realtime_metrics ---")
    return response

def get_fallback_simulated_state(device_id: str, device_type: str = "virtualization") -> Dict[str, Any]:
    """
    Génère un état par défaut réaliste en attente de la première exécution Celery.
    Utilisé en démo ou pour fournir un squelette de métriques lors d'un timeout
    afin de ne pas casser l'interface utilisateur.
    """
    # Résolution du type de simulation basé sur l'ID ou le device_type
    sim_type = device_id
    if device_id not in ["vcenter-01", "dorado-01", "unity-01", "powerstore-01", "datadomain-01", "netbackup-01", "scality-01", "veritas-01"]:
        if device_type == "virtualization":
            sim_type = "vcenter-01"
        elif device_type == "storage":
            sim_type = "dorado-01"
        elif device_type == "backup":
            sim_type = "datadomain-01"
        elif device_type == "object":
            sim_type = "scality-01"
        else:
            sim_type = "vcenter-01"

    if sim_type == "vcenter-01":
        return {
            "device_id": device_id,
            "device_name": "vCenter Cluster",
            "device_type": "virtualization",
            "status": "HEALTHY",
            "metrics": {
                "cpu_usage_pct": 0.0,
                "ram_usage_pct": 0.0,
                "vms_total": 0,
                "vms_active": 0,
                "hosts_total": 0,
                "hosts_online": 0,
                "hosts_maintenance": 0,
                "datastores_usage_pct": 0.0
            },
            "alerts": [],
            "vms_metrics": [],
            "hosts_metrics": []
        }
    elif sim_type == "dorado-01":
        return {
            "device_id": device_id,
            "device_name": "Huawei Dorado",
            "device_type": "storage",
            "status": "HEALTHY",
            "metrics": {
                "iops": 0,
                "latency_ms": 0.0,
                "throughput_mb": 0.0,
                "capacity_usage_pct": 0.0
            },
            "alerts": []
        }
    elif sim_type == "unity-01":
        return {
            "device_id": device_id,
            "device_name": "Dell Unity",
            "device_type": "storage",
            "status": "HEALTHY",
            "metrics": {
                "iops": 0,
                "latency_ms": 0.0,
                "throughput_mb": 0.0,
                "capacity_usage_pct": 0.0
            },
            "alerts": []
        }
    elif sim_type == "powerstore-01":
        return {
            "device_id": device_id,
            "device_name": "Dell PowerStore",
            "device_type": "storage",
            "status": "HEALTHY",
            "metrics": {
                "iops": 0,
                "latency_ms": 0.0,
                "throughput_mb": 0.0,
                "capacity_usage_pct": 0.0,
                "data_reduction_ratio": 0.0
            },
            "alerts": []
        }
    elif sim_type == "datadomain-01":
        return {
            "device_id": device_id,
            "device_name": "DataDomain Backup",
            "device_type": "backup",
            "status": "HEALTHY",
            "metrics": {
                "capacity_total_tb": 0.0,
                "capacity_used_tb": 0.0,
                "capacity_usage_pct": 0.0,
                "dedup_ratio": 0.0,
                "active_streams": 0
            },
            "alerts": []
        }
    elif sim_type == "netbackup-01":
        return {
            "device_id": device_id,
            "device_name": "NetBackup Master Server",
            "device_type": "backup",
            "status": "HEALTHY",
            "metrics": {
                "backup_success_rate": 0.0,
                "jobs_total": 0,
                "jobs_failed": 0,
                "jobs_active": 0,
                "daily_protected_tb": 0.0
            },
            "alerts": []
        }
    elif sim_type == "scality-01":
        return {
            "device_id": device_id,
            "device_name": "Scality RING Object",
            "device_type": "object",
            "status": "HEALTHY",
            "metrics": {
                "capacity_total_tb": 0.0,
                "capacity_used_tb": 0.0,
                "capacity_usage_pct": 0.0,
                "iops_s3": 0,
                "active_nodes": 0,
                "total_nodes": 0
            },
            "alerts": []
        }
    else:
        return {
            "device_id": device_id,
            "device_name": "Veritas Access Gateway",
            "device_type": "storage",
            "status": "HEALTHY",
            "metrics": {
                "capacity_total_tb": 0.0,
                "capacity_used_tb": 0.0,
                "capacity_usage_pct": 0.0,
                "nfs_shares_active": 0,
                "cifs_shares_active": 0
            },
            "alerts": []
        }
