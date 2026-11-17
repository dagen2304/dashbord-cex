import logging
import requests
from datetime import datetime, timedelta, timezone
from typing import Dict, Any

from app.services.base_collector import BaseCollector

logger = logging.getLogger(__name__)


class NetBackupCollector(BaseCollector):
    """
    Collecteur de production pour Veritas NetBackup Master Server.

    API  : REST NetBackup API sur HTTPS port 1556
    Auth : POST /netbackup/login → Bearer Token (API Key ou user/password)

    Métriques collectées :
        - Jobs actifs / total / échoués sur les dernières 24h
        - Taux de succès des sauvegardes (%)
        - Volume de données protégées quotidiennement (TB)
        - Statut des Media Servers
        - Alertes de jobs critiques
    """

    _DEFAULT_PORT = 1556
    _TIMEOUT = 60
    _API_VERSION = "v4.0"

    def collect(self) -> Dict[str, Any]:
        logger.info(f"[NetBackup] Démarrage collecte pour '{self.device_name}' ({self.config.get('host')})")
        return self._collect_real()

    def _collect_real(self) -> Dict[str, Any]:
        payload = self.get_base_payload(status="HEALTHY")
        payload["device_type"] = "backup"

        host = self.config.get("host")
        port = int(self.config.get("port", self._DEFAULT_PORT))
        user = self.config.get("username", "")
        password = self.config.get("password", "")
        api_key = self.config.get("api_key", "")  # Alternative: API Key
        base_url = f"https://{host}:{port}/netbackup"

        session = self.http_session()  # politique TLS de l'équipement (verify / pin / insecure)
        session.headers.update({
            "Content-Type": "application/vnd.netbackup+json; version=4.0",
            "Accept": "application/vnd.netbackup+json; version=4.0",
        })

        auth_token = None

        try:
            # ── 1. Authentification ───────────────────────────────────────
            if api_key:
                # Mode API Key (préféré pour les comptes de service)
                session.headers.update({"Authorization": f"Bearer {api_key}"})
                logger.info(f"[NetBackup] Utilisation de l'API Key pour {host}:{port}")
            else:
                # Mode Login/Password
                logger.info(f"[NetBackup] Authentification user/password sur {base_url}/login")
                login_resp = session.post(
                    f"{base_url}/login",
                    json={
                        "userName": user,
                        "password": password,
                        "domainType": "UNIXPWD",
                        "domainName": host,
                    },
                    timeout=self._TIMEOUT,
                )
                login_resp.raise_for_status()
                auth_token = login_resp.json().get("token")
                if not auth_token:
                    raise ValueError(f"Token absent dans la réponse login: {login_resp.text[:200]}")
                session.headers.update({"Authorization": f"Bearer {auth_token}"})
                logger.info(f"[NetBackup] Authentifié avec succès")

            # ── 2. Jobs des dernières 24 heures ───────────────────────────
            now = datetime.now(timezone.utc)
            since_24h = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")

            jobs_resp = session.get(
                f"{base_url}/admin/jobs",
                params={
                    "filter": f"startTime ge '{since_24h}'",
                    "page[limit]": 200,
                },
                timeout=self._TIMEOUT,
            )
            jobs_resp.raise_for_status()
            jobs_data = jobs_resp.json().get("data", [])

            jobs_total = len(jobs_data)
            jobs_failed = 0
            jobs_active = 0
            total_protected_bytes = 0

            for job in jobs_data:
                attrs = job.get("attributes", {})
                status_code = attrs.get("status", 0)
                state = attrs.get("state", "")

                # États NetBackup : QUEUED, ACTIVE, DONE
                if state == "ACTIVE":
                    jobs_active += 1

                # Codes de status : 0=succès, >0=erreur
                if state == "DONE" and status_code != 0:
                    jobs_failed += 1

                # Volume de données transféré
                kb_written = attrs.get("kiloBytesWritten", 0) or 0
                total_protected_bytes += kb_written * 1024

            backup_success_rate = round(
                ((jobs_total - jobs_failed) / jobs_total * 100), 2
            ) if jobs_total > 0 else 100.0

            daily_protected_tb = round(total_protected_bytes / (1024 ** 4), 2)

            # ── 3. Media Servers ──────────────────────────────────────────
            media_servers_total = 0
            media_servers_online = 0
            try:
                ms_resp = session.get(
                    f"{base_url}/config/media-servers",
                    timeout=self._TIMEOUT,
                )
                if ms_resp.ok:
                    ms_data = ms_resp.json().get("data", [])
                    media_servers_total = len(ms_data)
                    media_servers_online = sum(
                        1 for ms in ms_data
                        if ms.get("attributes", {}).get("status", "INACTIVE") == "ACTIVE"
                    )
            except Exception as ms_err:
                logger.warning(f"[NetBackup] Media servers indisponibles: {ms_err}")

            # ── 4. Alertes et statut ──────────────────────────────────────
            alerts = []
            status = "HEALTHY"

            if backup_success_rate < 95:
                status = "CRITICAL" if backup_success_rate < 90 else "WARNING"
                alerts.append({
                    "severity": "CRITICAL" if backup_success_rate < 90 else "WARNING",
                    "message": f"Taux de succès sauvegardes : {backup_success_rate}% ({jobs_failed} jobs échoués sur {jobs_total})",
                    "component": "Jobs"
                })

            if media_servers_total > 0 and media_servers_online < media_servers_total:
                status = "WARNING"
                alerts.append({
                    "severity": "WARNING",
                    "message": f"Media Servers hors ligne : {media_servers_online}/{media_servers_total}",
                    "component": "MediaServer"
                })

            # Récupérer les jobs en FAILED les plus récents comme alertes
            failed_jobs = [
                j for j in jobs_data
                if j.get("attributes", {}).get("state") == "DONE"
                and j.get("attributes", {}).get("status", 0) != 0
            ][:3]

            for fj in failed_jobs:
                attrs = fj.get("attributes", {})
                alerts.append({
                    "severity": "WARNING",
                    "message": f"Job échoué : {attrs.get('policyName', 'N/A')} → Code {attrs.get('status', '?')}",
                    "component": "JobPolicy"
                })

            payload["status"] = status
            payload["alerts"] = alerts
            payload["metrics"] = {
                "backup_success_rate": backup_success_rate,
                "jobs_total": jobs_total,
                "jobs_failed": jobs_failed,
                "jobs_active": jobs_active,
                "daily_protected_tb": daily_protected_tb,
                "media_servers_total": media_servers_total,
                "media_servers_online": media_servers_online,
            }

            logger.info(
                f"[NetBackup] ✓ {jobs_total} jobs, {jobs_failed} échoués, "
                f"Succès={backup_success_rate}%, Données={daily_protected_tb} TB"
            )
            return payload

        except Exception as e:
            logger.error(f"[NetBackup] ✗ Erreur de collecte pour {host}: {e}")
            payload["status"] = "OFFLINE"
            payload["alerts"] = [{
                "severity": "CRITICAL",
                "message": f"Connexion REST NetBackup {host}:{port} échouée — {type(e).__name__}: {e}",
                "component": "NetBackup-REST"
            }]
            payload["metrics"] = {}
            return payload

        finally:
            # ── Déconnexion propre ────────────────────────────────────────
            if auth_token:
                try:
                    session.post(f"{base_url}/logout", timeout=60)
                except Exception:
                    pass
            session.close()
