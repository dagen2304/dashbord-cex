import logging
import requests
from typing import Dict, Any

from app.services.base_collector import BaseCollector

logger = logging.getLogger(__name__)


class PowerStoreCollector(BaseCollector):
    """
    Collecteur de production pour les baies NVMe Dell PowerStore.

    API  : REST PowerStore Manager sur HTTPS port 443
    Auth : Basic Auth (user/password)

    Métriques collectées :
        - IOPS (read + write) depuis performance metrics query
        - Latence R/W (ms)
        - Débit (MB/s)
        - Capacité physique cluster (physical_total / physical_used / %)
        - Ratio de réduction de données (data_reduction_ratio)
    """

    _DEFAULT_PORT = 443
    _TIMEOUT = 60

    def collect(self) -> Dict[str, Any]:
        logger.info(f"[PowerStore] Démarrage collecte pour '{self.device_name}' ({self.config.get('host')})")
        return self._collect_real()

    def _collect_real(self) -> Dict[str, Any]:
        payload = self.get_base_payload(status="HEALTHY")
        payload["device_type"] = "storage"

        host = self.config.get("host")
        port = int(self.config.get("port", self._DEFAULT_PORT))
        user = self.config.get("username", "")
        password = self.config.get("password", "")

        # Éviter le port explicite si c'est 443
        if not port or port == 443:
            base_url = f"https://{host}/api/rest"
        else:
            base_url = f"https://{host}:{port}/api/rest"

        session = self.http_session()  # politique TLS de l'équipement (verify / pin / insecure)
        session.auth = (user, password)
        session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

        try:
            # 1. Appliance ID & Info
            r = session.get(f"{base_url}/appliance?select=*", timeout=60)
            r.raise_for_status()
            appl = r.json()[0]
            app_id = appl.get("id")
            
            firmware = appl.get("software_version", "N/A")
            if firmware == "N/A":
                firmware = f"{appl.get('software_version_major')}.{appl.get('software_version_minor')}"
            
            # 2. Capacity Metrics (Try multiple paths like in cex-monitor)
            cap_found = False
            total_bytes = 0
            used_bytes = 0
            
            for path in ["/space_metrics", "/appliance_metrics"]:
                try:
                    r_cap = session.get(f"{base_url}{path}?appliance_id=eq.{app_id}&order=timestamp.desc&limit=1", timeout=60)
                    if r_cap.status_code == 200 and r_cap.json():
                        m = r_cap.json()[0]
                        total_bytes = m.get("physical_total", m.get("physical_total_bytes", 0))
                        used_bytes = m.get("physical_used", m.get("physical_used_bytes", 0))
                        if total_bytes > 0:
                            cap_found = True
                            break
                except:
                    continue

            # Fallback to logical capacity volume sum if metrics access is denied
            if not cap_found:
                try:
                    r_vols = session.get(f"{base_url}/volume?select=id,name,size,appliance_id&appliance_id=eq.{app_id}", timeout=60)
                    if r_vols.status_code == 200:
                        vols = r_vols.json()
                        total_logical_bytes = sum(v.get("size", 0) for v in vols)
                        if total_logical_bytes > 0:
                            total_bytes = total_logical_bytes
                            used_bytes = total_logical_bytes
                            cap_found = True
                except:
                    pass

            total_tb = round(total_bytes / (1024 ** 4), 2)
            used_tb = round(used_bytes / (1024 ** 4), 2)
            usage_pct = round((used_bytes / total_bytes * 100), 1) if total_bytes > 0 else 0.0

            # 3. Performance Metrics
            iops = 0
            latency_ms = 0.0
            throughput_mb = 0.0
            
            for path in ["/performance_metrics_by_appliance", "/metrics/appliance"]:
                try:
                    r_perf = session.get(f"{base_url}{path}?appliance_id=eq.{app_id}&order=timestamp.desc&limit=1", timeout=60)
                    if r_perf.status_code == 200 and r_perf.json():
                        p = r_perf.json()[0]
                        iops = int(p.get("read_iops", 0) or 0) + int(p.get("write_iops", 0) or 0)
                        latency_ms = round((p.get("avg_latency", 0) or 0) / 1000, 2)
                        throughput_mb = round((p.get("total_bandwidth", 0) or 0) / (1024**2), 1)
                        break
                except:
                    continue

            # 4. Status and DRR from cluster info
            r_cluster = session.get(f"{base_url}/cluster?select=*", timeout=60)
            data_reduction_ratio = 1.0
            if r_cluster.status_code == 200:
                cluster_info = r_cluster.json()[0]
                ps_state = cluster_info.get("state", "unknown")
                status_map = {"Healthy": "Green", "Configured": "Green", "Minor_Failure": "Yellow", "Major_Failure": "Red"}
                payload["status"] = "HEALTHY" if status_map.get(ps_state, "Gray") == "Green" else "WARNING"
                
                dr = cluster_info.get("data_reduction_ratio_with_system_data_space")
                if dr:
                    try:
                        data_reduction_ratio = round(float(dr), 1)
                    except ValueError:
                        pass

            # 5. Alerts
            active_alerts = []
            r_alerts = session.get(f"{base_url}/alert?select=id,severity,description,resource_name,timestamp&severity=neq.Info&is_acknowledged=eq.false", timeout=60)
            if r_alerts.status_code == 200:
                for alert in r_alerts.json():
                    sev = alert.get("severity", "Warning").upper()
                    sev_str = "CRITICAL" if sev in ("CRITICAL", "MAJOR") else "WARNING"
                    active_alerts.append({
                        "severity": sev_str,
                        "message": alert.get("description", "Alerte système PowerStore"),
                        "component": alert.get("resource_name", "Appliance")
                    })

            payload["alerts"] = active_alerts
            payload["metrics"] = {
                "iops": iops,
                "latency_ms": latency_ms,
                "throughput_mb": throughput_mb,
                "capacity_total_tb": total_tb,
                "capacity_used_tb": used_tb,
                "capacity_usage_pct": usage_pct,
                "data_reduction_ratio": data_reduction_ratio,
            }

            logger.info(
                f"[PowerStore] ✓ IOPS={iops}, Latence={latency_ms}ms, "
                f"Capacité={usage_pct}% ({used_tb}/{total_tb} TB), DRR={data_reduction_ratio}x (Firmware: {firmware})"
            )
            return payload

        except Exception as e:
            logger.error(f"[PowerStore] ✗ Erreur de collecte pour {host}: {e}")
            payload["status"] = "OFFLINE"
            payload["alerts"] = [{
                "severity": "CRITICAL",
                "message": f"Connexion REST Dell PowerStore {host}:{port} échouée — {type(e).__name__}: {e}",
                "component": "PowerStore-REST"
            }]
            payload["metrics"] = {}
            return payload

        finally:
            session.close()
