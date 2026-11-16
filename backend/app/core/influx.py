import os
import random
import logging
from typing import Dict, Any, List
from datetime import datetime, timedelta
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

logger = logging.getLogger(__name__)

# Paramètres InfluxDB via variables d'environnement
INFLUXDB_URL = os.getenv("INFLUXDB_URL", "http://influxdb:8086")
INFLUXDB_TOKEN = os.getenv("INFLUXDB_TOKEN")
if not INFLUXDB_TOKEN:
    raise RuntimeError("INFLUXDB_TOKEN doit être défini dans l'environnement")
INFLUXDB_ORG = os.getenv("INFLUXDB_ORG", "noc_org")
INFLUXDB_BUCKET = os.getenv("INFLUXDB_BUCKET", "noc_metrics")

class InfluxDBManager:
    def __init__(self):
        self.client = None
        self.write_api = None
        self.query_api = None
        
        try:
            self.client = InfluxDBClient(url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG)
            self.write_api = self.client.write_api(write_options=SYNCHRONOUS)
            self.query_api = self.client.query_api()
            logger.info("Manager InfluxDB initialisé avec succès")
        except Exception as e:
            logger.error(f"Impossible d'initialiser le client InfluxDB : {str(e)}")

    def write_metrics(self, device_id: str, device_type: str, metrics: Dict[str, Any]):
        """
        Écrit un dictionnaire de métriques sous forme de Point temporel.
        """
        if not self.write_api:
            logger.warning("Écriture annulée : InfluxDB non connecté")
            return
            
        try:
            point = Point("noc_telemetry") \
                .tag("device_id", device_id) \
                .tag("device_type", device_type) \
                .time(datetime.utcnow(), WritePrecision.NS)
                
            for name, val in metrics.items():
                if isinstance(val, (int, float)):
                    point.field(name, float(val))
                    
            self.write_api.write(bucket=INFLUXDB_BUCKET, org=INFLUXDB_ORG, record=point)
            logger.debug(f"Métriques enregistrées pour {device_id} dans InfluxDB")
        except Exception as e:
            logger.error(f"Erreur d'écriture dans InfluxDB pour {device_id} : {str(e)}")

    def write_vm_telemetry(self, device_id: str, vms_metrics: List[Dict[str, Any]]):
        """
        Écrit les métriques individuelles de chaque VM dans InfluxDB (mesure 'vm_telemetry').
        """
        if not self.write_api:
            return
            
        try:
            points = []
            timestamp = datetime.utcnow()
            for vm in vms_metrics:
                point = Point("vm_telemetry") \
                    .tag("device_id", device_id) \
                    .tag("vm_name", vm["vm_name"]) \
                    .tag("vcenter", vm["vcenter"]) \
                    .tag("cluster", vm["cluster"]) \
                    .tag("os", vm["os"]) \
                    .tag("ip", vm["ip"]) \
                    .time(timestamp, WritePrecision.NS)
                    
                point.field("cpu_usage_pct", float(vm["cpu_usage_pct"]))
                point.field("ram_usage_pct", float(vm["ram_usage_pct"]))
                if "storage_gb" in vm:
                    point.field("storage_gb", float(vm["storage_gb"]))
                if "read_latency_ms" in vm:
                    point.field("read_latency_ms", float(vm["read_latency_ms"]))
                if "write_latency_ms" in vm:
                    point.field("write_latency_ms", float(vm["write_latency_ms"]))
                points.append(point)
                
            if points:
                self.write_api.write(bucket=INFLUXDB_BUCKET, org=INFLUXDB_ORG, record=points)
                logger.debug(f"{len(points)} métriques VM enregistrées pour {device_id}")
        except Exception as e:
            logger.error(f"Erreur d'écriture VM telemetry dans InfluxDB pour {device_id} : {str(e)}")

    def write_vm_fs_telemetry(self, device_id: str, vms_fs_metrics: List[Dict[str, Any]]):
        """
        Écrit les métriques des file systems invités (guest partitions) dans InfluxDB (mesure 'vm_fs_telemetry').
        """
        if not self.write_api:
            return
            
        try:
            points = []
            timestamp = datetime.utcnow()
            for fs in vms_fs_metrics:
                point = Point("vm_fs_telemetry") \
                    .tag("device_id", device_id) \
                    .tag("vm_name", fs["vm_name"]) \
                    .tag("vcenter", fs["vcenter"]) \
                    .tag("cluster", fs["cluster"]) \
                    .tag("mount_point", fs["mount_point"]) \
                    .time(timestamp, WritePrecision.NS)
                    
                point.field("capacity_gb", float(fs["capacity_gb"]))
                point.field("free_gb", float(fs["free_gb"]))
                point.field("used_gb", float(fs["used_gb"]))
                point.field("usage_pct", float(fs["usage_pct"]))
                points.append(point)
                
            if points:
                self.write_api.write(bucket=INFLUXDB_BUCKET, org=INFLUXDB_ORG, record=points)
                logger.debug(f"{len(points)} métriques de File Systems VM enregistrées pour {device_id}")
        except Exception as e:
            logger.error(f"Erreur d'écriture VM FS telemetry dans InfluxDB pour {device_id} : {str(e)}")

    def write_ds_telemetry(self, device_id: str, ds_metrics: List[Dict[str, Any]]):
        """
        Écrit les métriques individuelles des Datastores dans InfluxDB (mesure 'ds_telemetry').
        """
        if not self.write_api:
            return
            
        try:
            points = []
            timestamp = datetime.utcnow()
            for ds in ds_metrics:
                point = Point("ds_telemetry") \
                    .tag("device_id", device_id) \
                    .tag("datastore_name", ds["datastore_name"]) \
                    .time(timestamp, WritePrecision.NS)
                    
                if "read_latency_ms" in ds:
                    point.field("read_latency_ms", float(ds["read_latency_ms"]))
                if "write_latency_ms" in ds:
                    point.field("write_latency_ms", float(ds["write_latency_ms"]))
                
                point.field("usage_pct", float(ds.get("usage_pct", 0)))
                points.append(point)
                
            if points:
                self.write_api.write(bucket=INFLUXDB_BUCKET, org=INFLUXDB_ORG, record=points)
                logger.debug(f"{len(points)} métriques Datastore enregistrées pour {device_id}")
        except Exception as e:
            logger.error(f"Erreur d'écriture DS telemetry dans InfluxDB pour {device_id} : {str(e)}")

    def get_metric_history(self, device_id: str, field_name: str, hours: int = 24) -> List[float]:
        """
        Récupère l'historique d'une métrique pour les N dernières heures sous forme de liste de valeurs.
        Idéal pour alimenter les sparklines du Frontend.
        """
        if not self.query_api:
            # Retourne un historique simulé par défaut si InfluxDB n'est pas prêt
            return [round(random.uniform(30, 80), 1) for _ in range(12)]
            
        # Assainissement des paramètres pour prévenir l'injection Flux
        safe_device_id = device_id.replace('\\', '\\\\').replace('"', '\\"')
        safe_field_name = field_name.replace('\\', '\\\\').replace('"', '\\"')
        
        query = f'''
        from(bucket: "{INFLUXDB_BUCKET}")
          |> range(start: -{hours}h)
          |> filter(fn: (r) => r["_measurement"] == "noc_telemetry")
          |> filter(fn: (r) => r["device_id"] == "{safe_device_id}")
          |> filter(fn: (r) => r["_field"] == "{safe_field_name}")
          |> keep(columns: ["_time", "_value"])
          |> sort(columns: ["_time"])
        '''
        
        try:
            result = self.query_api.query(org=INFLUXDB_ORG, query=query)
            values = []
            for table in result:
                for record in table.records:
                    values.append(record.get_value())
            return values
        except Exception as e:
            logger.error(f"Erreur de lecture InfluxDB pour {device_id} (champ: {field_name}) : {str(e)}")
            return []

    def get_vm_latency_history(self, hours: int = 6) -> List[Dict[str, Any]]:
        """
        Récupère l'historique des latences disques (Read/Write) pour les VMs.
        Renvoie un tableau regroupé par VM.
        """
        if not self.query_api:
            return []
            
        # Utilisation de Flux pour pivoter les champs read_latency_ms et write_latency_ms
        query = f'''
        from(bucket: "{INFLUXDB_BUCKET}")
          |> range(start: -{hours}h)
          |> filter(fn: (r) => r["_measurement"] == "vm_telemetry")
          |> filter(fn: (r) => r["_field"] == "read_latency_ms" or r["_field"] == "write_latency_ms")
          |> pivot(rowKey:["_time", "vm_name", "device_id"], columnKey: ["_field"], valueColumn: "_value")
          |> keep(columns: ["_time", "vm_name", "device_id", "read_latency_ms", "write_latency_ms"])
          |> sort(columns: ["_time"])
        '''
        
        try:
            result = self.query_api.query(org=INFLUXDB_ORG, query=query)
            vms_data = {}
            for table in result:
                for record in table.records:
                    vm = record.values.get("vm_name")
                    if not vm:
                        continue
                        
                    if vm not in vms_data:
                        vms_data[vm] = {
                            "vm_name": vm,
                            "device_id": record.values.get("device_id"),
                            "history": []
                        }
                    
                    vms_data[vm]["history"].append({
                        "timestamp": record.get_time().isoformat(),
                        "read_latency_ms": record.values.get("read_latency_ms") or 0.0,
                        "write_latency_ms": record.values.get("write_latency_ms") or 0.0
                    })
            
            return list(vms_data.values())
        except Exception as e:
            logger.error(f"Erreur de lecture InfluxDB pour get_vm_latency_history : {str(e)}")
            return []

    def get_objects_list(self) -> Dict[str, List[str]]:
        """
        Récupère la liste des VMs et des Datastores ayant remonté des métriques récemment.
        """
        if not self.query_api:
            return {"Virtual Machine": [], "Datastore": []}
            
        # Requête Flux pour extraire les noms de VMs uniques
        vm_query = f'''
        import "influxdata/influxdb/schema"
        schema.tagValues(bucket: "{INFLUXDB_BUCKET}", tag: "vm_name")
        '''
        
        ds_query = f'''
        import "influxdata/influxdb/schema"
        schema.tagValues(bucket: "{INFLUXDB_BUCKET}", tag: "datastore_name")
        '''
        
        try:
            vm_result = self.query_api.query(org=INFLUXDB_ORG, query=vm_query)
            vms = []
            for table in vm_result:
                for record in table.records:
                    if record.get_value():
                        vms.append(record.get_value())
                        
            ds_result = self.query_api.query(org=INFLUXDB_ORG, query=ds_query)
            datastores = []
            for table in ds_result:
                for record in table.records:
                    if record.get_value():
                        datastores.append(record.get_value())
            
            return {"Virtual Machine": sorted(vms), "Datastore": sorted(datastores)}
        except Exception as e:
            logger.error(f"Erreur de lecture InfluxDB pour get_objects_list : {str(e)}")
            return {"Virtual Machine": [], "Datastore": []}

    def get_dynamic_metric_history(self, object_type: str, object_name: str, metric_field: str, hours: int = 12) -> List[Dict[str, Any]]:
        """
        Récupère l'historique d'une métrique spécifique pour un objet donné.
        """
        if not self.query_api:
            return []
            
        measurement = "vm_telemetry" if object_type == "Virtual Machine" else "ds_telemetry"
        tag_key = "vm_name" if object_type == "Virtual Machine" else "datastore_name"
        
        # Assainissement des paramètres pour prévenir l'injection Flux
        safe_object_name = object_name.replace('\\', '\\\\').replace('"', '\\"')
        safe_metric_field = metric_field.replace('\\', '\\\\').replace('"', '\\"')
        
        query = f'''
        from(bucket: "{INFLUXDB_BUCKET}")
          |> range(start: -{hours}h)
          |> filter(fn: (r) => r["_measurement"] == "{measurement}")
          |> filter(fn: (r) => r["{tag_key}"] == "{safe_object_name}")
          |> filter(fn: (r) => r["_field"] == "{safe_metric_field}")
          |> keep(columns: ["_time", "_value"])
          |> sort(columns: ["_time"])
        '''
        
        try:
            result = self.query_api.query(org=INFLUXDB_ORG, query=query)
            data = []
            for table in result:
                for record in table.records:
                    data.append({
                        "timestamp": record.get_time().isoformat(),
                        "value": record.get_value()
                    })
            return data
        except Exception as e:
            logger.error(f"Erreur de lecture InfluxDB dynamique : {str(e)}")
            return []

# Singleton
influx_manager = InfluxDBManager()
