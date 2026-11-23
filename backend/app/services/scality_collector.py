import logging
import requests
from typing import Dict, Any

from app.services.base_collector import BaseCollector

logger = logging.getLogger(__name__)


class ScalityCollector(BaseCollector):
    """
    Collecteur de production pour le stockage objet Scality RING.

    API  : REST RING Supervisor sur HTTP/HTTPS (port 80 ou 443)
    Auth : Basic Auth sur l'API supervisor Scality

    Métriques collectées :
        - IOPS S3 (requêtes/seconde)
        - Capacité totale du RING (total / utilisé / %)
        - Nœuds actifs / total
        - Santé des disques (disk_health)
        - Bande passante entrante/sortante (MB/s)
    """

    _DEFAULT_PORT = 443
    _TIMEOUT = 60

    def collect(self) -> Dict[str, Any]:
        logger.info(f"[Scality] Démarrage collecte pour '{self.device_name}' ({self.config.get('host')})")
        return self._collect_real()

    def _collect_real(self) -> Dict[str, Any]:
        payload = self.get_base_payload(status="HEALTHY")
        payload["device_type"] = "object"

        host = self.config.get("host")
        port = int(self.config.get("port", self._DEFAULT_PORT))
        user = self.config.get("username", "")
        password = self.config.get("password", "")

        # Éviter le port explicite si c'est 443
        if not port or port == 443:
            host_port = host
        else:
            host_port = f"{host}:{port}"

        session = self.http_session()  # politique TLS de l'équipement (verify / pin / insecure)
        if user and password:
            session.auth = (user, password)
            
        # Pas de Content-Type sur les GET : l'API Supervisor (Flask / Eve) tente alors de décoder
        # un corps JSON vide et répond HTTP 400 « The browser (or proxy) sent a request that
        # this server could not understand ».
        session.headers.update({"Accept": "application/json"})

        protocol = "http" if port in (80, 8080) else "https"
        
        try:
            # 1. Get Supervisor Status / Version
            url_status = f"{protocol}://{host_port}/api/v0.1/status/"
            r_status = session.get(url_status, timeout=60)
            firmware = "N/A"
            if r_status.status_code == 200:
                status_data = r_status.json()
                firmware = f"RING {status_data.get('supapi_version', 'N/A')}"
                
            # 2. Get Rings Data
            url_rings = f"{protocol}://{host_port}/api/v0.1/rings/"
            r_rings = session.get(url_rings, timeout=60)
            
            if r_rings.status_code == 200:
                rings_data = r_rings.json()
                items = rings_data.get("_items", [])
                
                # Find the DATA ring (primary storage), or use the first available ring
                data_ring = next((r for r in items if r.get("id") == "DATA" or r.get("name") == "DATA"), None)
                if not data_ring and items:
                    data_ring = items[0]
                    
                if data_ring:
                    logger.info(f"[Scality] Collecte réussie pour {self.device_name} ({host}) - Ring: {data_ring.get('name')} (Firmware: {firmware})")
                    
                    # Aucune valeur inventée : un champ absent reste absent (ni 0, ni copie d'un autre).
                    # L'ancien code mettait iops_s3 à 0 en dur et active_nodes = total_nodes.
                    metrics: Dict[str, Any] = {}
                    not_collected = []
                    total_bytes, used_bytes = data_ring.get("diskspace_total"), data_ring.get("diskspace_used")
                    if isinstance(total_bytes, (int, float)) and total_bytes > 0 and isinstance(used_bytes, (int, float)):
                        metrics.update(capacity_total_tb=round(total_bytes / 1024 ** 4, 2),     # octets → To
                                       capacity_used_tb=round(used_bytes / 1024 ** 4, 2),
                                       capacity_usage_pct=round(used_bytes / total_bytes * 100, 1))
                    else:
                        not_collected.append("capacité (diskspace_total / diskspace_used absents)")
                    if isinstance(data_ring.get("number_of_nodes"), int):
                        metrics["total_nodes"] = data_ring["number_of_nodes"]
                    ring_status = data_ring.get("status")
                    alerts = []
                    if ring_status == "OK":
                        payload["status"] = "HEALTHY"
                    else:
                        payload["status"] = "WARNING"
                        alerts.append({"severity": "WARNING", "component": f"Ring {data_ring.get('name') or data_ring.get('id')}",
                                       "message": f"État du ring : {ring_status or 'inconnu'}"})
                    if not_collected:
                        alerts.append({"severity": "INFO", "component": "RING-Supervisor",
                                       "message": "Non collecté, laissé vide : " + " ; ".join(not_collected)})
                    payload["metrics"] = metrics
                    payload["alerts"] = alerts
                else:
                    raise Exception("Aucun Ring trouvé dans la réponse Scality.")
            else:
                raise Exception(f"Rings API HTTP {r_rings.status_code} - {r_rings.text}")

            return payload

        except Exception as e:
            logger.error(f"[Scality] ✗ Erreur de collecte pour {host}: {e}")
            payload["status"] = "OFFLINE"
            self.mark_failure(payload, e)
            payload["alerts"] = [{
                "severity": "CRITICAL",
                "message": f"Connexion Scality RING {host}:{port} échouée — {type(e).__name__}: {e}",
                "component": "RING-Supervisor"
            }]
            payload["metrics"] = {}
            return payload

        finally:
            session.close()
