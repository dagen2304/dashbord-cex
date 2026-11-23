import logging
import requests
from typing import Any, Dict, Optional

from app.services.base_collector import BaseCollector

logger = logging.getLogger(__name__)


class UnityCollector(BaseCollector):
    """
    Collecteur de production pour les baies de stockage Dell Unity (XT).

    API  : REST Unisphere sur HTTPS port 443
    Session (guide « Unisphere Management REST API ») :
      - connexion : GET /api/types/loginSessionInfo en Basic Auth → cookie de session
        + en-tête EMC-CSRF-TOKEN (obligatoire sur les POST) ;
      - requêtes suivantes : cookie uniquement (les identifiants ne sont plus renvoyés,
        sinon chaque requête peut ouvrir une nouvelle session) ;
      - déconnexion : POST /api/types/loginSessionInfo/action/logout, toujours tentée
        dès qu'une session a été ouverte, même si la collecte échoue. Sans elle, les
        sessions s'accumulent jusqu'à la limite de la baie.

    Métriques collectées :
        - IOPS et débit (lecture + écriture) : historique de métriques de la baie
          (types/metricValue, chemins sp.*.storage.summary.*Rate, sommés sur les SP)
        - Latence : non collectée (pas de valeur au niveau de la baie dans cet historique)
        - Capacité des pools (sizeTotal / sizeUsed / %)
        - État de santé du système (systemHealth)
    """

    _DEFAULT_PORT = 443
    _TIMEOUT = 60


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

        logged_in = False
        try:
            # ── 1. Ouverture de session + jeton CSRF ──────────────────────
            login_resp = session.get(f"{base_url}/types/loginSessionInfo", timeout=self._TIMEOUT)
            login_resp.raise_for_status()
            logged_in = True
            csrf_token = login_resp.headers.get("EMC-CSRF-TOKEN", "")
            if csrf_token:
                session.headers.update({"EMC-CSRF-TOKEN": csrf_token})
            else:
                logger.warning(f"[Unity] {host} : pas de jeton EMC-CSRF-TOKEN (la déconnexion risque d'être refusée)")
            # La suite utilise le cookie de session : on ne renvoie plus les identifiants.
            session.auth = None
            logger.info(f"[Unity] Session ouverte sur {host}")

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
            # Historique des métriques Unity (collecte intégrée de la baie, 60 s par défaut) :
            # dernière valeur de chaque chemin, sommée sur les deux SP. Aucune valeur par
            # défaut : une mesure absente reste absente (jamais 0) et la cause est signalée.
            perf_metrics: Dict[str, Any] = {}
            not_collected: list = []
            rates = {path: self._latest_metric(session, base_url, path) for path in self._PERF_PATHS}
            missing = [p for p, v in rates.items() if v is None]
            if rates["sp.*.storage.summary.readsRate"] is not None and rates["sp.*.storage.summary.writesRate"] is not None:
                perf_metrics["iops"] = round(rates["sp.*.storage.summary.readsRate"] + rates["sp.*.storage.summary.writesRate"])
            if rates["sp.*.storage.summary.readBytesRate"] is not None and rates["sp.*.storage.summary.writeBytesRate"] is not None:
                perf_metrics["throughput_mb"] = round((rates["sp.*.storage.summary.readBytesRate"]
                                                       + rates["sp.*.storage.summary.writeBytesRate"]) / 1024 ** 2, 1)
            if missing:
                not_collected.append("performance : " + ", ".join(missing) + " sans valeur récente "
                                     "(collecte des métriques désactivée sur la baie, ou droits du compte)")

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
            if not_collected:
                active_alerts.append({"severity": "INFO", "component": "Unisphere-REST",
                                      "message": "Non collecté, laissé vide : " + " ; ".join(not_collected)})
            payload["metrics"] = {
                **perf_metrics,
                "capacity_total_tb": total_tb,
                "capacity_used_tb": used_tb,
                "capacity_usage_pct": usage_pct,
            }

            logger.info(
                f"[Unity] ✓ IOPS={perf_metrics.get('iops', '—')}, Débit={perf_metrics.get('throughput_mb', '—')}Mo/s, "
                f"Capacité={usage_pct}% ({used_tb}/{total_tb} TB), Santé={status}"
            )
            return payload

        except Exception as e:
            logger.error(f"[Unity] ✗ Erreur de collecte pour {host}: {e}")
            payload["status"] = "OFFLINE"
            self.mark_failure(payload, e)
            payload["alerts"] = [{
                "severity": "CRITICAL",
                "message": f"Connexion REST Dell Unity {host}:{port} échouée — {type(e).__name__}: {e}",
                "component": "Unisphere-REST"
            }]
            payload["metrics"] = {}
            return payload

        finally:
            if logged_in:
                self._logout(session, base_url, host)
            session.close()

    # Chemins de l'historique de métriques Unity : opérations et octets par seconde, par SP.
    _PERF_PATHS = ("sp.*.storage.summary.readsRate", "sp.*.storage.summary.writesRate",
                   "sp.*.storage.summary.readBytesRate", "sp.*.storage.summary.writeBytesRate")

    def _latest_metric(self, session, base_url: str, path: str) -> Optional[float]:
        """Dernière valeur d'un chemin de métrique, sommée sur les SP ; None si indisponible."""
        try:
            resp = session.get(f"{base_url}/types/metricValue/instances", timeout=self._TIMEOUT,
                               params={"filter": f'path EQ "{path}"', "compact": "true", "per_page": "5"})
            if not resp.ok:
                return None
            entries = [e.get("content", {}) for e in resp.json().get("entries", [])]
            entries = [e for e in entries if isinstance(e.get("values"), dict) and e["values"]]
            if not entries:
                return None
            latest = max(entries, key=lambda e: e.get("timestamp") or "")
            values = [float(v) for v in latest["values"].values() if isinstance(v, (int, float, str)) and str(v) != ""]
            return sum(values) if values else None
        except Exception as exc:
            logger.warning("[Unity] Métrique %s indisponible : %s", path, exc)
            return None

    def _logout(self, session, base_url: str, host: str) -> None:
        """Ferme la session REST côté baie (localCleanupOnly : n'affecte pas une éventuelle session SSO)."""
        try:
            resp = session.post(f"{base_url}/types/loginSessionInfo/action/logout",
                                json={"localCleanupOnly": True}, timeout=15)
            if resp.status_code in (200, 202, 204):
                logger.info(f"[Unity] Session fermée sur {host}")
            else:
                logger.warning(f"[Unity] Déconnexion refusée par {host} : HTTP {resp.status_code} — session laissée ouverte")
        except Exception as exc:
            logger.warning(f"[Unity] Déconnexion impossible sur {host} : {type(exc).__name__}: {exc}")
