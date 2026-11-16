from typing import Optional
from datetime import datetime
from pydantic import BaseModel, ConfigDict

class DeviceDiscoveryInput(BaseModel):
    """
    Payload reçu pour lancer le scan d'Auto-Discovery.
    """
    hostname_ip: str
    username: str
    password: str

class DeviceDiscoveryResponse(BaseModel):
    """
    Réponse du scanner d'Auto-Discovery.
    """
    detected_type: str
    recommended_port: int
    category: str
    status: str

class DeviceBase(BaseModel):
    display_name: str
    hostname_ip: str
    device_type: str
    category: str
    port: int
    username: str

class DeviceCreate(DeviceBase):
    """
    Payload de création finale de l'équipement (avec mot de passe en clair à chiffrer).
    """
    password: str

class DeviceUpdate(BaseModel):
    """
    Payload de modification de l'équipement (tous les champs sont optionnels).
    """
    display_name: Optional[str] = None
    hostname_ip: Optional[str] = None
    device_type: Optional[str] = None
    category: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None
    is_active: Optional[bool] = None

class DeviceResponse(DeviceBase):
    """
    Réponse renvoyée à l'UI (DeviceContext / DeviceSelector).
    Le mot de passe chiffré n'est JAMAIS renvoyé pour des raisons de sécurité.
    """
    id: str
    is_active: bool
    is_archived: bool = False          # Soft Delete — False par défaut pour compatibilité
    last_sync_status: str
    last_sync_at: Optional[datetime] = None   # Horodatage du dernier cycle Celery réussi

    model_config = ConfigDict(from_attributes=True)


class SystemSettingBase(BaseModel):
    key: str
    value: str
    description: Optional[str] = None
    category: str = "GENERAL"

class SystemSettingResponse(SystemSettingBase):
    model_config = ConfigDict(from_attributes=True)

class SystemSettingUpdate(BaseModel):
    value: str

