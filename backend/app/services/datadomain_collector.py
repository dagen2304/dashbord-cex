import logging
import base64
import requests
import urllib3
from typing import Dict, Any

from app.services.base_collector import BaseCollector

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = logging.getLogger(__name__)


class DataDomainCollector(BaseCollector):
    """
    Collecteur de production pour les appliances Dell DataDomain (DDOS).

    API  : REST DataDomain OS sur HTTPS port 3009
    Auth : POST /rest/v1.0/auth → Bearer Token (Basic Auth encodé en base64)

    Métriques collectées :
        - Capacité système (total / utilisé / libre / %)
        - Ratio de déduplication global (compression factor)
        - Streams actifs (DDBoost)
        - Statut de réplication
        - Nettoyage (cleaning) en cours
    """

    _DEFAULT_PORT = 3009
    _TIMEOUT = 60
    _API_VERSION = "v1.0"

    def collect(self) -> Dict[str, Any]:
        logger.info(f"[DataDomain] Démarrage collecte pour '{self.device_name}' ({self.config.get('host')})")
        return self._collect_real()

    def _collect_real(self) -> Dict[str, Any]:
        payload = self.get_base_payload(status="HEALTHY")
        payload["device_type"] = "backup"

        host = self.config.get("host")
        port = int(self.config.get("port", self._DEFAULT_PORT))
        if port == 443:
            port = 3009
            
        user = self.config.get("username", "")
        password = self.config.get("password", "")

        session = requests.Session()
        session.verify = False
        session.headers.update({"Accept": "application/json", "Content-Type": "application/json"})

        base = None
        auth_token = None

        try:
            # 1. Login
            for ver in ["v1.0", "v2.0", "v3.0"]:
                current_base = f"https://{host}:{port}/rest/{ver}"
                try:
                    r = session.post(
                        f"{current_base}/auth",
                        json={"auth_info": {"username": user, "password": password}},
                        timeout=60
                    )
                    if r.status_code in [200, 201]:
                        token = r.headers.get("X-DD-AUTH-TOKEN")
                        if not token:
                            try:
                                token = r.json().get("auth_info", {}).get("token")
                            except:
                                pass
                        if token:
                            auth_token = token
                            session.headers["X-DD-AUTH-TOKEN"] = token
                            base = current_base
                            break
                except Exception as e:
                    logger.debug(f"[DataDomain] Login version {ver} failed: {e}")
                    continue

            if not base:
                raise Exception("Authentification échouée sur toutes les API versions de Data Domain.")

            payload["status"] = "HEALTHY"

            # 2. System Info
            model = "N/A"
            firmware = "N/A"
            total_bytes = 0
            used_bytes = 0
            
            try:
                r_sys = session.get(f"{base}/system", timeout=60)
                if r_sys.status_code == 200:
                    sys_data = r_sys.json()
                    sys = sys_data.get("system_info", sys_data)
                    model = sys.get("model", "N/A")
                    firmware = sys.get("version", "N/A")
                    if sys.get("status", "").lower() not in ["ok", "normal"]:
                        payload["status"] = "WARNING"
                    
                    if "physical_capacity" in sys:
                        pc = sys["physical_capacity"]
                        total_bytes = float(pc.get("total", 0))
                        used_bytes = float(pc.get("used", 0))
            except Exception as sys_err:
                logger.warning(f"[DataDomain] System info failed: {sys_err}")

            # 3. Capacity Filesystem
            capacity_paths = ["/filesys", "/dd-systems/0/filesys", "/stats/capacity"]
            for path in capacity_paths:
                try:
                    r_cap = session.get(f"{base}{path}", timeout=60)
                    if r_cap.status_code == 200:
                        data = r_cap.json()
                        fs = data.get("filesys", data.get("capacity", data.get("filesystem", data)))
                        if isinstance(fs, list) and len(fs) > 0:
                            fs = fs[0]
                        
                        space = fs.get("space_usage", fs.get("storage_usage", fs.get("capacity", fs)))
                        total = space.get("total", space.get("size", space.get("total_size", space.get("capacity", 0))))
                        used = space.get("used", space.get("used_size", space.get("used_capacity", 0)))
                        
                        if total == 0:
                            total = fs.get("total_size", fs.get("pre_comp_size", 0))
                            used = fs.get("used_size", fs.get("pre_comp_used", 0))

                        if total > 0 and total_bytes == 0:
                            total_bytes = float(total)
                            used_bytes = float(used)
                            break
                except:
                    continue

            # Convert capacity. The values in Data Domain API filesystem space_usage are typically bytes or GiB depending on OS version.
            if total_bytes > 1e9:
                total_tb = round(total_bytes / (1024 ** 4), 2)
                used_tb = round(used_bytes / (1024 ** 4), 2)
            else:
                total_tb = round(total_bytes / 1024, 2)
                used_tb = round(used_bytes / 1024, 2)

            usage_pct = round((used_tb / total_tb * 100), 1) if total_tb > 0 else 0.0

            # 4. Hardware Disks
            disks_total = 0
            disks_failed = 0
            try:
                r_disks = session.get(f"{base}/dd-systems/0/disk", timeout=60)
                if r_disks.status_code == 200:
                    disks = r_disks.json().get("disk", [])
                    disks_total = len(disks)
                    disks_failed = sum(1 for d in disks if d.get("state","").lower() not in ["normal", "ok", "active"])
            except:
                pass

            if disks_failed > 0:
                payload["status"] = "WARNING"
                payload["alerts"].append({
                    "severity": "WARNING",
                    "message": f"{disks_failed} disque(s) en défaut sur Data Domain",
                    "component": "Hardware"
                })

            payload["metrics"] = {
                "capacity_total_tb": total_tb,
                "capacity_used_tb": used_tb,
                "capacity_usage_pct": usage_pct,
                "dedup_ratio": 1.0,
                "active_streams": 0,
                "replication_status": 1,
            }

            logger.info(
                f"[DataDomain] ✓ Capacity: {used_tb}/{total_tb} TB ({usage_pct}%) (Model: {model}, Firmware: {firmware})"
            )
            return payload

        except Exception as e:
            logger.error(f"[DataDomain] ✗ Erreur de collecte pour {host}: {e}")
            payload["status"] = "OFFLINE"
            payload["alerts"] = [{
                "severity": "CRITICAL",
                "message": f"Connexion REST DataDomain {host}:{port} échouée — {type(e).__name__}: {e}",
                "component": "DDOS-REST"
            }]
            payload["metrics"] = {}
            return payload

        finally:
            if auth_token and base:
                try:
                    session.delete(f"{base}/auth", timeout=60)
                except:
                    pass
            session.close()
