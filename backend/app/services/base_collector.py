from abc import ABC, abstractmethod
from typing import Dict, Any, List
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class CredentialsRejected(PermissionError):
    """L'équipement a refusé les identifiants (mauvais mot de passe, compte verrouillé…)."""


def is_auth_error(exc: BaseException) -> bool:
    """Vrai si l'échec vient des identifiants, et non du réseau, du TLS ou de l'équipement.

    Un tel échec ne doit PAS être retenté : chaque nouvel essai rapproche le compte de
    service de son verrouillage (sur l'équipement, ou dans l'Active Directory)."""
    if isinstance(exc, PermissionError):
        return True
    response = getattr(exc, "response", None)
    if response is not None and getattr(response, "status_code", None) == 401:
        return True
    name = type(exc).__name__
    # pyVmomi : vim.fault.InvalidLogin ; paramiko : AuthenticationException, BadAuthenticationType…
    return name in {"InvalidLogin", "AuthenticationException", "BadAuthenticationType",
                    "PasswordRequiredException", "NotAuthenticated"}

class BaseCollector(ABC):
    """
    Interface commune pour tous les collecteurs d'équipements de la plateforme.
    Assure une standardisation complète des données de télémétrie récoltées.
    """
    
    def __init__(self, device_id: str, device_name: str, config: Dict[str, Any]):
        self.device_id = device_id
        self.device_name = device_name
        self.config = config
        # Plus aucun mode simulation dans le code de production (les simulateurs vivent dans tests/).
        self.simulate = False

    @abstractmethod
    def collect(self) -> Dict[str, Any]:
        """
        Méthode abstraite à implémenter pour chaque type de matériel.
        Doit interroger l'API ou simuler le retour et renvoyer un dictionnaire normalisé.
        """
        pass

    # ── Connexions vérifiées (voir app/services/tls.py) ──────────────────────
    @property
    def tls_policy(self):
        from app.services.tls import TlsPolicy
        return TlsPolicy.from_config(self.config)

    def http_session(self):
        """Session HTTPS appliquant la politique TLS de l'équipement (verify / pin / insecure)."""
        from app.services.tls import http_session
        return http_session(self.tls_policy, self.device_name)

    @staticmethod
    def mark_failure(payload: Dict[str, Any], exc: BaseException) -> None:
        """À appeler dans le bloc d'échec final : signale au planificateur un refus d'identifiants."""
        if is_auth_error(exc):
            payload["auth_failed"] = True

    def get_base_payload(self, status: str = "HEALTHY") -> Dict[str, Any]:
        """
        Génère la structure JSON commune normalisée pour tous les collecteurs.
        """
        return {
            "device_id": self.device_id,
            "device_name": self.device_name,
            "status": status,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "metrics": {},
            "alerts": []
        }
