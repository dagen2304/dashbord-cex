import logging
import random
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Query, Depends
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.inventory import InfrastructureDevice, AnomalySaturation
from app.core.influx import influx_manager, INFLUXDB_BUCKET, INFLUXDB_ORG

logger = logging.getLogger(__name__)
router = APIRouter()

@router.get("/filters/clusters")
async def get_clusters_for_vcenter(vcenter: str = Query(..., description="Le vCenter pour lequel on souhaite lister les clusters")):
    """
    Retourne la liste des clusters distincts pour un vCenter donné,
    en interrogeant directement les données temporelles InfluxDB.
    """
    if not influx_manager.query_api:
        return []
        
    try:
        query = f'''
        import "influxdata/influxdb/schema"
        schema.tagValues(
            bucket: "{INFLUXDB_BUCKET}",
            tag: "cluster",
            predicate: (r) => r._measurement == "vm_telemetry" and r.vcenter == "{vcenter}"
        )
        '''
        result = influx_manager.query_api.query(org=INFLUXDB_ORG, query=query)
        
        clusters = []
        for table in result:
            for record in table.records:
                val = record.get_value()
                if val and val not in clusters:
                    clusters.append(val)
                    
        return sorted(clusters)
    except Exception as e:
        logger.error(f"Erreur lors de la récupération des clusters pour {vcenter} : {str(e)}")
        return []

@router.get("/saturation")
async def get_vm_saturation(
    start_date: datetime = Query(default=None),
    end_date: datetime = Query(default=None),
    vcenter: Optional[str] = Query(default=None),
    cluster: Optional[str] = Query(default=None),
    threshold: float = Query(default=90.0),
    duration_minutes: int = Query(default=15),
    device_id: Optional[str] = Query(default=None),
    db: Session = Depends(get_db)
):
    """
    Détecte et liste les VMs ayant subi des saturations de ressources (CPU/RAM).
    Interroge PostgreSQL (table anomalies_saturation) ou InfluxDB, et bascule sur simulation en repli.
    """
    if not start_date:
        start_date = datetime.utcnow() - timedelta(days=1)
    if not end_date:
        end_date = datetime.utcnow()
        
    logger.info(f"Requête de diagnostic de saturation: {start_date} -> {end_date}, vCenter={vcenter}, Cluster={cluster}, Seuil={threshold}%, Durée={duration_minutes}m, device_id={device_id}")
    
    # 1. Tenter la requête PostgreSQL relationnelle d'abord
    try:
        q = db.query(AnomalySaturation).filter(
            AnomalySaturation.timestamp >= start_date,
            AnomalySaturation.timestamp <= end_date,
            AnomalySaturation.max_value >= threshold,
            AnomalySaturation.duration_minutes >= duration_minutes
        )
        if device_id:
            q = q.filter(AnomalySaturation.device_id == device_id)
        if vcenter and vcenter != 'all':
            q = q.filter(AnomalySaturation.vcenter == vcenter)
        if cluster and cluster != 'all':
            q = q.filter(AnomalySaturation.cluster == cluster)
            
        db_anomalies = q.all()
        if db_anomalies:
            logger.info(f"Détection de {len(db_anomalies)} saturations réelles via PostgreSQL (anomalies_saturation)")
            return [
                {
                    "vm_name": a.vm_name,
                    "vcenter": a.vcenter,
                    "cluster": a.cluster,
                    "resource": a.resource,
                    "max_value": a.max_value,
                    "duration_minutes": a.duration_minutes,
                    "os": a.os or "Linux RHEL 8",
                    "ip": a.ip or "10.0.0.10"
                }
                for a in db_anomalies
            ]
    except Exception as e:
        logger.error(f"Erreur lors de la requête PostgreSQL de saturation : {str(e)}")

    # 2. Tenter la requête InfluxDB réelle si possible
    if influx_manager.query_api:
        try:
            start_str = start_date.strftime("%Y-%m-%dT%H:%M:%SZ")
            end_str = end_date.strftime("%Y-%m-%dT%H:%M:%SZ")
            
            # Construction du filtre vCenter/Cluster
            tag_filters = ""
            if vcenter and vcenter != 'all':
                tag_filters += f' |> filter(fn: (r) => r["vcenter"] == "{vcenter}")'
            if cluster and cluster != 'all':
                tag_filters += f' |> filter(fn: (r) => r["cluster"] == "{cluster}")'
                
            query = f'''
            from(bucket: "{INFLUXDB_BUCKET}")
              |> range(start: {start_str}, stop: {end_str})
              |> filter(fn: (r) => r["_measurement"] == "vm_telemetry")
              |> filter(fn: (r) => r["_field"] == "cpu_usage_pct" or r["_field"] == "ram_usage_pct")
              {tag_filters}
              |> stateDuration(fn: (r) => r._value > {threshold}, column: "saturated_duration", unit: 1m)
              |> filter(fn: (r) => r.saturated_duration >= {duration_minutes})
              |> group(columns: ["vm_name", "vcenter", "cluster", "_field"])
              |> max(column: "_value")
              '''
            
            result = influx_manager.query_api.query(org=INFLUXDB_ORG, query=query)
            
            real_data = []
            for table in result:
                for record in table.records:
                    # Traiter les records d'anomalies réelles détectées
                    vm_name = record.values.get("vm_name")
                    vc = record.values.get("vcenter")
                    clust = record.values.get("cluster")
                    field = record.get_field()
                    resource = "CPU" if "cpu" in field else "RAM"
                    max_val = record.get_value()
                    duration = record.values.get("saturated_duration", duration_minutes)
                    
                    real_data.append({
                        "vm_name": vm_name,
                        "vcenter": vc,
                        "cluster": clust,
                        "resource": resource,
                        "max_value": round(max_val, 1),
                        "duration_minutes": int(duration),
                        "os": "Linux RHEL 8",
                        "ip": "10.0.0.10"
                    })
                    
            logger.info(f"Détection de {len(real_data)} saturations réelles via InfluxDB")
            return real_data
                
        except Exception as e:
            logger.error(f"Erreur lors de la requête InfluxDB de saturation : {str(e)}")
            
    return []


@router.get("/saturation/{vm_name}/history")
async def get_vm_saturation_history(
    vm_name: str,
    resource: str = Query(default="CPU"),
    start_date: datetime = Query(default=None),
    end_date: datetime = Query(default=None),
    threshold: float = Query(default=90.0)
):
    """
    Retourne la série temporelle d'une VM spécifique pour alimenter le graphique historique des pics de charge.
    """
    if not start_date:
        start_date = datetime.utcnow() - timedelta(days=1)
    if not end_date:
        end_date = datetime.utcnow()
        
    logger.info(f"Historique de charge de la VM {vm_name} ({resource}): {start_date} -> {end_date}")
    
    # 1. Tenter la requête InfluxDB réelle si possible
    if influx_manager.query_api:
        try:
            start_str = start_date.strftime("%Y-%m-%dT%H:%M:%SZ")
            end_str = end_date.strftime("%Y-%m-%dT%H:%M:%SZ")
            field = "cpu_usage_pct" if resource == "CPU" else "ram_usage_pct"
            
            query = f'''
            from(bucket: "{INFLUXDB_BUCKET}")
              |> range(start: {start_str}, stop: {end_str})
              |> filter(fn: (r) => r["_measurement"] == "vm_telemetry")
              |> filter(fn: (r) => r["vm_name"] == "{vm_name}")
              |> filter(fn: (r) => r["_field"] == "{field}")
              |> keep(columns: ["_time", "_value"])
              |> sort(columns: ["_time"])
            '''
            
            result = influx_manager.query_api.query(org=INFLUXDB_ORG, query=query)
            history_points = []
            for table in result:
                for record in table.records:
                    history_points.append({
                        "timestamp": record.get_time().isoformat(),
                        "value": round(record.get_value(), 1)
                    })
                    
            return history_points
        except Exception as e:
            logger.error(f"Erreur historique de charge InfluxDB : {str(e)}")
            
    return []

@router.get("/filesystems")
async def get_vm_filesystems(
    vcenter: Optional[str] = Query(default=None),
    cluster: Optional[str] = Query(default=None)
):
    """
    Récupère les dernières métriques des filesystems des VMs depuis InfluxDB.
    """
    if not influx_manager.query_api:
        return []

    try:
        tag_filters = ""
        if vcenter and vcenter != 'all':
            tag_filters += f' |> filter(fn: (r) => r["vcenter"] == "{vcenter}")'
        if cluster and cluster != 'all':
            tag_filters += f' |> filter(fn: (r) => r["cluster"] == "{cluster}")'

        query = f'''
        from(bucket: "{INFLUXDB_BUCKET}")
          |> range(start: -15m)
          |> filter(fn: (r) => r["_measurement"] == "vm_fs_telemetry")
          {tag_filters}
          |> last()
        '''
        
        result = influx_manager.query_api.query(org=INFLUXDB_ORG, query=query)
        
        fs_metrics = {}
        for table in result:
            for record in table.records:
                vm_name = record.values.get("vm_name")
                mount = record.values.get("mount_point")
                vc = record.values.get("vcenter")
                clust = record.values.get("cluster")
                field = record.get_field()
                val = record.get_value()
                
                key = f"{vm_name}_{mount}"
                if key not in fs_metrics:
                    fs_metrics[key] = {
                        "id": key,
                        "vm_name": vm_name,
                        "vcenter": vc,
                        "cluster": clust,
                        "mount_point": mount,
                        "capacity_gb": 0.0,
                        "free_gb": 0.0,
                        "used_gb": 0.0,
                        "usage_pct": 0.0
                    }
                fs_metrics[key][field] = round(val, 2) if isinstance(val, (float, int)) else val

        # Sort by usage_pct descending by default
        result_list = list(fs_metrics.values())
        result_list.sort(key=lambda x: x["usage_pct"], reverse=True)
        return result_list
        
    except Exception as e:
        logger.error(f"Erreur lors de la récupération des file systems VMs : {str(e)}")
        return []
