import logging
import re
import requests
import json
from typing import Dict, Any, Optional

from app.services.base_collector import BaseCollector, CredentialsRejected

logger = logging.getLogger(__name__)


class DoradoCollector(BaseCollector):
    """
    Collecteur de production pour les baies de stockage Huawei Dorado (OceanStor).

    API  : REST OceanStor v2 sur HTTPS (port 8088 par défaut)
    Auth : POST /deviceManager/rest/v2/sessions → { iBaseToken, deviceid }

    Métriques collectées :
        - IOPS (lecture + écriture) via performance_statistic
        - Latence R/W (ms)
        - Débit (MB/s)
        - Capacité des pools de stockage (total / utilisé / %)
        - Nombre de contrôleurs online
    """

    _DEFAULT_PORT = 8088
    _TIMEOUT = 60  # secondes

    # IDs de compteurs de performance OceanStor (stables sur Dorado V6/V7)
    _PERF_COUNTERS = {
        "iops_read":   "22",
        "iops_write":  "23",
        "latency_read":  "57",
        "latency_write": "58",
        "throughput":    "21",
    }

    def collect(self) -> Dict[str, Any]:
        logger.info(f"[Dorado] Démarrage collecte pour '{self.device_name}' ({self.config.get('host')})")
        return self._collect_real()



    def _collect_real(self) -> Dict[str, Any]:
        payload = self.get_base_payload(status="HEALTHY")
        payload["device_type"] = "storage"

        host = self.config.get("host")
        port = int(self.config.get("port", self._DEFAULT_PORT))
        user = self.config.get("username", "")
        password = self.config.get("password", "")
        base_url = f"https://{host}:{port}/deviceManager/rest"

        session = self.http_session()  # politique TLS de l'équipement (verify / pin / insecure)
        device_id = None
        ibase_token = None

        # Gestion du scope (0 = Local, 1 = LDAP) via extra_params
        extra_params = self.config.get("extra_params") or {}
        if isinstance(extra_params, str):
            try:
                extra_params = json.loads(extra_params)
            except:
                extra_params = {}
        auth_scope = int(extra_params.get("scope", 0))
        # Compte LDAP / AD de la baie : saisir « LDAP\utilisateur » dans l'inventaire (scope 1).
        if "\\" in user:
            prefix, name = user.split("\\", 1)
            if prefix.strip().upper() == "LDAP":
                user, auth_scope = name, 1
            elif prefix.strip().upper() in ("LOCAL", ""):
                user, auth_scope = name, 0

        try:
            # ── 1. Authentification ───────────────────────────────────────
            logger.info(f"[Dorado] Authentification sur {base_url}/v2/sessions (scope={auth_scope})")
            auth_resp = session.post(
                f"{base_url}/v2/sessions",
                json={"username": user, "password": password, "scope": auth_scope},
                timeout=self._TIMEOUT,
            )
            auth_resp.raise_for_status()
            # La baie répond HTTP 200 même si le mot de passe est faux : l'échec est dans error.code.
            auth_error = (auth_resp.json() or {}).get("error") or {}
            if auth_error.get("code") not in (None, 0, "0"):
                detail = auth_error.get("description") or "refus de connexion"
                if re.search(r"password|user ?name|locked|lock|authenticat|scope", detail, re.I):
                    raise CredentialsRejected(
                        f"Identifiants refusés par la baie (code {auth_error.get('code')}) : {detail} "
                        f"[compte {'LDAP' if auth_scope == 1 else 'local'}]")
                raise ValueError(f"Connexion refusée par la baie (code {auth_error.get('code')}) : {detail}")
            auth_data = auth_resp.json().get("data", {})
            ibase_token = auth_data.get("iBaseToken")
            device_id = auth_data.get("deviceid")

            if not ibase_token or not device_id:
                raise ValueError(f"Authentification échouée — réponse invalide: {auth_resp.text[:200]}")

            session.headers.update({"iBaseToken": ibase_token})
            logger.info(f"[Dorado] Authentifié — deviceId={device_id}")

            base = f"https://{host}:{port}/deviceManager/rest/{device_id}"

            # ── 2. Pools de stockage ──────────────────────────────────────
            pool_resp = session.get(
                f"{base}/storagepool",
                timeout=self._TIMEOUT,
            )
            pool_resp.raise_for_status()
            pools = pool_resp.json().get("data", [])

            total_capacity_bytes = 0
            used_capacity_bytes = 0
            for pool in pools:
                # OceanStor retourne les capacités en secteurs de 512 octets
                total_capacity_bytes += int(pool.get("USERTOTALCAPACITY", 0)) * 512
                used_capacity_bytes += int(pool.get("USERCONSUMEDCAPACITY", 0)) * 512

            total_tb = round(total_capacity_bytes / (1024 ** 4), 2)
            used_tb = round(used_capacity_bytes / (1024 ** 4), 2)
            usage_pct = round((used_capacity_bytes / total_capacity_bytes * 100), 1) if total_capacity_bytes > 0 else 0.0

            # ── 3. Métriques de performance (Dorado API performancestatistictask/0 comme dans cex-monitor) ──
            iops = 0
            latency_ms = 0.0
            throughput_mb = 0.0

            try:
                perf_resp = session.get(f"{base}/performancestatistictask/0", timeout=60)
                if perf_resp.ok and perf_resp.json().get("error", {}).get("code", -1) == 0:
                    p = perf_resp.json().get("data", {})
                    iops = round(float(p.get("IOPS", 0)), 0)
                    throughput_mb = round(float(p.get("BANDWIDTH", 0)) / 1024, 2)  # KB/s → MB/s
                    latency_ms = round(float(p.get("LATENCY", 0)) / 1000, 2)       # μs → ms
            except Exception as perf_err:
                logger.warning(f"[Dorado] Perf stats indisponibles: {perf_err}")

            # ── 4. Contrôleurs ────────────────────────────────────────────
            controllers_total = 0
            controllers_online = 0
            try:
                ctrl_resp = session.get(f"{base}/controller", timeout=self._TIMEOUT)
                if ctrl_resp.ok:
                    controllers = ctrl_resp.json().get("data", [])
                    controllers_total = len(controllers)
                    controllers_online = sum(
                        1 for c in controllers if c.get("HEALTHSTATUS") == "1"
                    )
            except Exception as ctrl_err:
                logger.warning(f"[Dorado] Contrôleurs indisponibles: {ctrl_err}")

            # ── 5. Alertes ────────────────────────────────────────────────
            alerts = []
            status = "HEALTHY"
            
            # Charger les alarmes depuis currentalarm
            try:
                alarm_resp = session.get(f"{base}/alarm/currentalarm?range=[0-99]", timeout=self._TIMEOUT)
                if alarm_resp.ok and alarm_resp.json().get("error", {}).get("code", -1) == 0:
                    HUAWEI_SEV = {"1": "CRITICAL", "2": "MAJOR", "3": "WARNING", "4": "INFO"}
                    for alarm in alarm_resp.json().get("data", []):
                        sev_code = str(alarm.get("alarmLevel", "4"))
                        sev_str = HUAWEI_SEV.get(sev_code, "INFO")
                        if sev_str in ["CRITICAL", "MAJOR"]:
                            status = "CRITICAL"
                        elif sev_str == "WARNING" and status == "HEALTHY":
                            status = "WARNING"
                        
                        alerts.append({
                            "severity": "CRITICAL" if sev_str in ["CRITICAL", "MAJOR"] else "WARNING",
                            "message": alarm.get("alarmName", alarm.get("description", "Alerte Dorado")),
                            "component": alarm.get("objectName", "Storage")
                        })
            except Exception as alarm_err:
                logger.warning(f"[Dorado] Alarmes indisponibles: {alarm_err}")

            if usage_pct > 80 and status == "HEALTHY":
                status = "WARNING"
                alerts.append({
                    "severity": "WARNING",
                    "message": f"Pools Dorado à {usage_pct}% de capacité ({used_tb} / {total_tb} TB)",
                    "component": "StoragePool"
                })

            payload["status"] = status
            payload["alerts"] = alerts
            payload["metrics"] = {
                "iops": iops,
                "latency_ms": latency_ms,
                "throughput_mb": throughput_mb,
                "capacity_total_tb": total_tb,
                "capacity_used_tb": used_tb,
                "capacity_usage_pct": usage_pct,
                "controllers_online": controllers_online,
                "controllers_total": controllers_total,
            }

            logger.info(
                f"[Dorado] ✓ IOPS={iops}, Latence={latency_ms}ms, "
                f"Capacité={usage_pct}% ({used_tb}/{total_tb} TB)"
            )
            return payload

        except Exception as e:
            logger.error(f"[Dorado] ✗ Erreur de collecte pour {host}: {e}")
            payload["status"] = "OFFLINE"
            self.mark_failure(payload, e)
            payload["alerts"] = [{
                "severity": "CRITICAL",
                "message": f"Connexion REST Huawei Dorado {host}:{port} échouée — {type(e).__name__}: {e}",
                "component": "OceanStor-REST"
            }]
            payload["metrics"] = {}
            return payload

        finally:
            # ── Déconnexion propre ────────────────────────────────────────
            if ibase_token:
                try:
                    session.delete(
                        f"{base_url}/v2/sessions",
                        headers={"iBaseToken": ibase_token},
                        timeout=60,
                    )
                except Exception:
                    pass
            session.close()
