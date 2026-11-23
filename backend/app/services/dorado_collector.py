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

    # HEALTHSTATUS / RUNNINGSTATUS d'un pool (API OceanStor / Dorado).
    _POOL_HEALTH = {"1": "NORMAL", "2": "FAULT", "5": "DEGRADED"}
    _POOL_RUNNING = {"27": "ONLINE", "28": "OFFLINE", "53": "INITIALIZING", "106": "DEGRADED"}

    @classmethod
    def _pool_row(cls, pool: Dict[str, Any]) -> Dict[str, Any]:
        """Un pool de stockage ; un champ absent de la réponse reste absent (aucune valeur inventée)."""
        def tb(key):
            try:
                return round(int(pool[key]) * 512 / 1024 ** 4, 2) if pool.get(key) not in (None, "") else None
            except (TypeError, ValueError):
                return None
        row: Dict[str, Any] = {"name": pool.get("NAME") or f"Pool {pool.get('ID', '?')}"}
        if pool.get("ID") is not None:
            row["id"] = str(pool["ID"])
        total, used, free, subscribed = (tb("USERTOTALCAPACITY"), tb("USERCONSUMEDCAPACITY"),
                                         tb("USERFREECAPACITY"), tb("SUBSCRIBEDCAPACITY"))
        if total:
            row["capacity_tb"] = total
            if used is not None:
                row["used_tb"] = used
                row["usage_pct"] = round(used / total * 100, 1)
            row["free_tb"] = free if free is not None else (round(total - used, 2) if used is not None else None)
            if subscribed is not None:
                row["subscribed_tb"] = subscribed
                row["subscription_pct"] = round(subscribed / total * 100, 1)
        try:
            row["alarm_threshold_pct"] = float(pool["USERCONSUMEDCAPACITYTHRESHOLD"])
        except (KeyError, TypeError, ValueError):
            pass
        if str(pool.get("HEALTHSTATUS")) in cls._POOL_HEALTH:
            row["health"] = cls._POOL_HEALTH[str(pool["HEALTHSTATUS"])]
        if str(pool.get("RUNNINGSTATUS")) in cls._POOL_RUNNING:
            row["running"] = cls._POOL_RUNNING[str(pool["RUNNINGSTATUS"])]
        return {k: v for k, v in row.items() if v is not None}

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
            storage_pools = []
            for pool in pools:
                # OceanStor retourne les capacités en secteurs de 512 octets
                total_capacity_bytes += int(pool.get("USERTOTALCAPACITY", 0)) * 512
                used_capacity_bytes += int(pool.get("USERCONSUMEDCAPACITY", 0)) * 512
                storage_pools.append(self._pool_row(pool))
            storage_pools.sort(key=lambda p: p.get("usage_pct", -1), reverse=True)

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

            # Alerte PAR POOL : un pool presque plein ne doit pas être masqué par la moyenne
            # de la baie (ex. un pool à 95 % et un autre à 30 % donnent 60 % au total).
            for sp in storage_pools:
                threshold = sp.get("alarm_threshold_pct") or 80.0
                if sp.get("usage_pct") is not None and sp["usage_pct"] >= threshold:
                    critical = sp["usage_pct"] >= max(90.0, threshold)
                    alerts.append({
                        "severity": "CRITICAL" if critical else "WARNING",
                        "message": f"Pool {sp['name']} à {sp['usage_pct']:g} % ({sp['used_tb']:g} / {sp['capacity_tb']:g} To, seuil {threshold:g} %)",
                        "component": "StoragePool",
                    })
                    if critical:
                        status = "CRITICAL"
                    elif status == "HEALTHY":
                        status = "WARNING"
                if sp.get("health") in ("FAULT", "DEGRADED"):
                    alerts.append({"severity": "CRITICAL" if sp["health"] == "FAULT" else "WARNING",
                                   "message": f"Pool {sp['name']} : état {'défaillant' if sp['health'] == 'FAULT' else 'dégradé'}",
                                   "component": "StoragePool"})
                    status = "CRITICAL" if sp["health"] == "FAULT" else ("WARNING" if status == "HEALTHY" else status)

            payload["status"] = status
            payload["alerts"] = alerts
            payload["storage_pools"] = storage_pools
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
