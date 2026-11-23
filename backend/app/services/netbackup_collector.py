import logging
import requests
from datetime import datetime, timedelta, timezone
from typing import Dict, Any

from app.services.base_collector import BaseCollector, CredentialsRejected

logger = logging.getLogger(__name__)


class NetBackupCollector(BaseCollector):
    """
    Collecteur de production pour Veritas NetBackup Master Server.

    API  : REST NetBackup API sur HTTPS port 1556
    Auth : POST /netbackup/login → jeton JWT, envoyé TEL QUEL dans « Authorization »
           (NetBackup n'utilise pas le préfixe « Bearer »).
           Compte Active Directory : saisir « DOMAINE\\utilisateur » (domainType NT) ;
           compte local du serveur primaire : « utilisateur » (domainType unixpwd).
    Adresse : le NOM du serveur tel qu'il figure dans son certificat web. Appelé par une
           autre adresse (ex. son IP), NetBackup refuse la connexion (errorCode 8963).

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

    @staticmethod
    def _nbu_error(resp) -> str:
        """Message d'erreur de NetBackup ({errorCode, errorMessage}), sinon début du corps."""
        try:
            body = resp.json()
            return f"{body.get('errorMessage') or body}" + (f" (code {body['errorCode']})" if body.get("errorCode") else "")
        except ValueError:
            return resp.text[:200] or f"HTTP {resp.status_code}"

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
                # Clé d'API NetBackup : envoyée telle quelle, sans « Bearer ».
                session.headers.update({"Authorization": api_key})
                logger.info(f"[NetBackup] Utilisation de l'API Key pour {host}:{port}")
            else:
                if "\\" in user:   # compte Active Directory : DOMAINE\utilisateur
                    domain_name, user_name = user.split("\\", 1)
                    domain_type = "NT"
                else:                # compte local du serveur primaire
                    domain_name, user_name, domain_type = host, user, "unixpwd"
                logger.info(f"[NetBackup] Authentification {domain_type} sur {base_url}/login")
                login_resp = session.post(
                    f"{base_url}/login",
                    json={"userName": user_name, "password": password,
                          "domainType": domain_type, "domainName": domain_name},
                    timeout=self._TIMEOUT,
                )
                if login_resp.status_code == 401:
                    raise CredentialsRejected(f"identifiants refusés par NetBackup ({self._nbu_error(login_resp)})")
                if not login_resp.ok:
                    raise ValueError(f"connexion refusée par NetBackup (HTTP {login_resp.status_code}) : {self._nbu_error(login_resp)}")
                auth_token = login_resp.json().get("token")
                if not auth_token:
                    raise ValueError("réponse de connexion sans jeton")
                session.headers.update({"Authorization": auth_token})
                logger.info(f"[NetBackup] Authentifié avec succès")

            # ── 2. Jobs des dernières 24 heures ───────────────────────────
            now = datetime.now(timezone.utc)
            since_24h = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")

            # Pages de 100 (maximum de l'API) ; garde-fou à 50 pages.
            jobs_data = []
            for page in range(50):
                jobs_resp = session.get(
                    f"{base_url}/admin/jobs",
                    params={"filter": f"startTime ge '{since_24h}'", "page[limit]": 100, "page[offset]": page * 100},
                    timeout=self._TIMEOUT,
                )
                if not jobs_resp.ok:
                    raise ValueError(f"lecture des jobs refusée (HTTP {jobs_resp.status_code}) : {self._nbu_error(jobs_resp)}")
                batch = jobs_resp.json().get("data", [])
                jobs_data.extend(batch)
                if len(batch) < 100:
                    break

            jobs_total = len(jobs_data)
            jobs_failed = 0
            jobs_active = 0
            total_protected_bytes = 0

            for job in jobs_data:
                attrs = job.get("attributes", {})
                status_code = attrs.get("status")   # absent = inconnu, pas « succès »
                state = attrs.get("state", "")

                # États NetBackup : QUEUED, ACTIVE, DONE
                if state == "ACTIVE":
                    jobs_active += 1

                # Codes de status : 0=succès, >0=erreur
                if state == "DONE" and status_code not in (None, 0, 1):   # 1 = succès partiel
                    jobs_failed += 1

                # Volume de données transféré
                kb_written = attrs.get("kiloBytesWritten", 0) or 0
                total_protected_bytes += kb_written * 1024

            finished = sum(1 for j in jobs_data if (j.get("attributes") or {}).get("state") == "DONE")
            # Aucun job terminé sur 24 h : taux inconnu (et non 100 %).
            backup_success_rate = round((finished - jobs_failed) / finished * 100, 2) if finished else None

            daily_protected_tb = round(total_protected_bytes / (1024 ** 4), 2)

            # ── 3. Media Servers ──────────────────────────────────────────
            media_servers_total = media_servers_online = None   # inconnus tant que non lus
            not_collected = []
            try:
                ms_resp = session.get(
                    f"{base_url}/config/media-servers",
                    timeout=self._TIMEOUT,
                )
                if not ms_resp.ok:
                    not_collected.append(f"media servers (HTTP {ms_resp.status_code})")
                else:
                    ms_data = ms_resp.json().get("data", [])
                    media_servers_total = len(ms_data)
                    media_servers_online = sum(
                        1 for ms in ms_data
                        if ms.get("attributes", {}).get("status", "INACTIVE") == "ACTIVE"
                    )
            except Exception as ms_err:
                logger.warning(f"[NetBackup] Media servers indisponibles: {ms_err}")
                not_collected.append(f"media servers ({type(ms_err).__name__})")

            # ── 4. Alertes et statut ──────────────────────────────────────
            alerts = []
            status = "HEALTHY"

            if backup_success_rate is not None and backup_success_rate < 95:
                status = "CRITICAL" if backup_success_rate < 90 else "WARNING"
                alerts.append({
                    "severity": "CRITICAL" if backup_success_rate < 90 else "WARNING",
                    "message": f"Taux de succès sauvegardes : {backup_success_rate}% ({jobs_failed} jobs échoués sur {jobs_total})",
                    "component": "Jobs"
                })

            if media_servers_total and media_servers_online < media_servers_total:
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
                and j.get("attributes", {}).get("status") not in (None, 0, 1)
            ][:3]

            for fj in failed_jobs:
                attrs = fj.get("attributes", {})
                alerts.append({
                    "severity": "WARNING",
                    "message": f"Job échoué : {attrs.get('policyName', 'N/A')} → Code {attrs.get('status', '?')}",
                    "component": "JobPolicy"
                })

            if not_collected:
                alerts.append({"severity": "INFO", "component": "NetBackup-REST",
                               "message": "Non collecté, laissé vide : " + " ; ".join(not_collected)})
            payload["status"] = status
            payload["alerts"] = alerts
            metrics = {
                "backup_success_rate": backup_success_rate,
                "jobs_total": jobs_total,
                "jobs_failed": jobs_failed,
                "jobs_active": jobs_active,
                "daily_protected_tb": daily_protected_tb,
                "media_servers_total": media_servers_total,
                "media_servers_online": media_servers_online,
            }
            payload["metrics"] = {k: v for k, v in metrics.items() if v is not None}

            logger.info(
                f"[NetBackup] ✓ {jobs_total} jobs, {jobs_failed} échoués, "
                f"Succès={backup_success_rate}%, Données={daily_protected_tb} TB"
            )
            return payload

        except Exception as e:
            logger.error(f"[NetBackup] ✗ Erreur de collecte pour {host}: {e}")
            payload["status"] = "OFFLINE"
            self.mark_failure(payload, e)
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
