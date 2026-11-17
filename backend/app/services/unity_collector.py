import logging
import requests
from typing import Dict, Any

from app.services.base_collector import BaseCollector

logger = logging.getLogger(__name__)


class UnityCollector(BaseCollector):
    """
    Collecteur de production pour les baies de stockage Dell Unity (XT).

    API  : REST Unisphere sur HTTPS port 443
    Auth : Basic Auth + Header EMC-CSRF-TOKEN (récupéré sur /api/types/loginSessionInfo)

    Métriques collectées :
        - IOPS (lecture + écriture) via metric real-time query
        - Latence R/W (ms)
        - Débit (MB/s)
        - Capacité des pools (sizeTotal / sizeUsed / %)
        - État de santé du système (systemHealth)
    """

    _DEFAULT_PORT = 443
    _TIMEOUT = 60

    # Chemins de métriques Dell Unity (API Unisphere v5+)
    _METRIC_PATHS = [
        "sp.*.physical.disk.reads",           # IOPS lecture
        "sp.*.physical.disk.writes",          # IOPS écriture
        "sp.*.physical.disk.readResponseTime", # Latence lecture (ms)
        "sp.*.physical.disk.writeResponseTime",# Latence écriture (ms)
        "sp.*.physical.disk.readBandwidth",   # Débit lecture (MB/s)
        "sp.*.physical.disk.writeBandwidth",  # Débit écriture (MB/s)
    ]

    def collect(self) -> Dict[str, Any]:
        logger.info(f"[Unity] Démarrage collecte pour '{self.device_name}' ({self.config.get('host')})")
        return self._collect_real()

    def _collect_real(self) -> Dict[str, Any]:
        payload = self.get_base_payload(status="HEALTHY")
        payload["device_type"] = "storage"

        host = self.config.get("host")
        port = int(self.config.get("port", self._DEFAULT_PORT))
        user = self.config.get("username", "")
        password = self.config.get("password", "")
        base_url = f"https://{host}:{port}/api"

        session = self.http_session()  # politique TLS de l'équipement (verify / pin / insecure)
        session.auth = (user, password)
        session.headers.update({
            "X-EMC-REST-CLIENT": "true",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

        try:
            # ── 1. Authentification + CSRF Token ──────────────────────────
            logger.info(f"[Unity] Authentification sur {base_url}/types/loginSessionInfo/instances")
            login_resp = session.get(
                f"{base_url}/types/loginSessionInfo/instances",
                params={"compact": "true"},
                timeout=self._TIMEOUT,
            )
            login_resp.raise_for_status()

            # Extraire le CSRF token des cookies/headers
            csrf_token = login_resp.headers.get("EMC-CSRF-TOKEN", "")
            if csrf_token:
                session.headers.update({"EMC-CSRF-TOKEN": csrf_token})
            logger.info(f"[Unity] Authentifié, CSRF={'présent' if csrf_token else 'absent'}")

            # ── 2. Santé du système ───────────────────────────────────────
            health_resp = session.get(
                f"{base_url}/types/system/instances",
                params={"compact": "true", "fields": "health,model,serialNumber,name"},
                timeout=self._TIMEOUT,
            )
            system_health = "UNKNOWN"
            if health_resp.ok:
                system_data = health_resp.json().get("entries", [{}])[0].get("content", {})
                health_value = system_data.get("health", {}).get("value", 5)
                # Dell Unity health values : 5=OK, 7=OK_BUT, 10=DEGRADED, 15=MINOR, 20=MAJOR, 25=CRITICAL, 30=NON_RECOVERABLE
                system_health = "HEALTHY" if health_value == 5 else (
                    "WARNING" if health_value <= 10 else "CRITICAL"
                )

            # ── 3. Pools de stockage ──────────────────────────────────────
            pools_resp = session.get(
                f"{base_url}/types/pool/instances",
                params={
                    "compact": "true",
                    "fields": "name,sizeFree,sizeTotal,sizeUsed,health"
                },
                timeout=self._TIMEOUT,
            )
            pools_resp.raise_for_status()

            total_bytes = 0
            used_bytes = 0
            pool_entries = pools_resp.json().get("entries", [])
            for entry in pool_entries:
                content = entry.get("content", {})
                total_bytes += int(content.get("sizeTotal", 0))
                used_bytes += int(content.get("sizeUsed", 0))

            total_tb = round(total_bytes / (1024 ** 4), 2)
            used_tb = round(used_bytes / (1024 ** 4), 2)
            usage_pct = round((used_bytes / total_bytes * 100), 1) if total_bytes > 0 else 0.0

            # ── 4. Métriques de performance temps réel ────────────────────
            iops = 0
            latency_ms = 0.0
            throughput_mb = 0.0

            try:
                # Utilisation de l'endpoint direct (plus rapide que metricRealTimeQuery avec sleep)
                perf_resp = session.get(
                    f"{base_url}/types/system/0",
                    params={"fields": "currIops,currBandwidth,currLatency"},
                    timeout=self._TIMEOUT,
                )
                if perf_resp.ok:
                    p = perf_resp.json().get("content", {})
                    iops = int(p.get("currIops", 0) or 0)
                    throughput_mb = round((p.get("currBandwidth", 0) or 0) / (1024 * 1024), 1)
                    latency_ms = round((p.get("currLatency", 0) or 0) / 1000, 2)
            except Exception as perf_err:
                logger.warning(f"[Unity] Métriques de performance indisponibles: {perf_err}")

            # ── 5. Alertes actives ────────────────────────────────────────
            alerts_resp = session.get(
                f"{base_url}/types/alert/instances",
                params={
                    "compact": "true",
                    "fields": "severity,message,component",
                    "filter": "state eq 0"  # 0 = ACTIVE (non acquitté)
                },
                timeout=self._TIMEOUT,
            )
            active_alerts = []
            if alerts_resp.ok:
                for entry in alerts_resp.json().get("entries", [])[:5]:  # Max 5 alertes
                    content = entry.get("content", {})
                    sev = content.get("severity", 8)
                    # Severity : 1=EMERGENCY, 2=ALERT, 3=CRITICAL, 4=ERROR, 5=WARNING, 8=NOTICE
                    sev_str = "CRITICAL" if sev <= 4 else "WARNING"
                    active_alerts.append({
                        "severity": sev_str,
                        "message": content.get("message", "Alerte système"),
                        "component": content.get("component", {}).get("id", "System")
                    })

            status = system_health
            if usage_pct > 85:
                status = "WARNING"
                active_alerts.append({
                    "severity": "WARNING",
                    "message": f"Capacité pools élevée : {usage_pct}% ({used_tb}/{total_tb} TB)",
                    "component": "StoragePool"
                })

            payload["status"] = status
            payload["alerts"] = active_alerts
            payload["metrics"] = {
                "iops": iops,
                "latency_ms": latency_ms,
                "throughput_mb": throughput_mb,
                "capacity_total_tb": total_tb,
                "capacity_used_tb": used_tb,
                "capacity_usage_pct": usage_pct,
            }

            logger.info(
                f"[Unity] ✓ IOPS={iops}, Latence={latency_ms}ms, "
                f"Capacité={usage_pct}% ({used_tb}/{total_tb} TB), Santé={status}"
            )
            return payload

        except Exception as e:
            logger.error(f"[Unity] ✗ Erreur de collecte pour {host}: {e}")
            payload["status"] = "OFFLINE"
            payload["alerts"] = [{
                "severity": "CRITICAL",
                "message": f"Connexion REST Dell Unity {host}:{port} échouée — {type(e).__name__}: {e}",
                "component": "Unisphere-REST"
            }]
            payload["metrics"] = {}
            return payload

        finally:
            try:
                session.delete(f"{base_url}/types/loginSessionInfo/instances/0", timeout=60)
            except Exception:
                pass
            session.close()
