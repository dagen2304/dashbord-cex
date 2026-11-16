from abc import ABC, abstractmethod
from typing import Dict, Any, List
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

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
