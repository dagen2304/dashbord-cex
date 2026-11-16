from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class DeviceDiscoveryInput(BaseModel):
    """Adresse à sonder. Aucun identifiant n'est nécessaire pour la détection."""
    hostname_ip: str = Field(min_length=1, max_length=253)


class DeviceDiscoveryResponse(BaseModel):
    detected_type: str
    recommended_port: int
    category: str
    status: str


class DeviceBase(BaseModel):
    display_name: str = Field(min_length=1, max_length=128)
    hostname_ip: str = Field(min_length=1, max_length=253)
    device_type: str
    port: int = Field(ge=1, le=65535)
    username: str = Field(min_length=1, max_length=128)


class DeviceCreate(DeviceBase):
    """Création. `category` est déduite du type côté serveur (champ toléré pour compatibilité)."""
    password: str = Field(min_length=1, max_length=512)
    category: Optional[str] = None


class DeviceUpdate(BaseModel):
    display_name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    hostname_ip: Optional[str] = Field(default=None, min_length=1, max_length=253)
    device_type: Optional[str] = None
    category: Optional[str] = None
    port: Optional[int] = Field(default=None, ge=1, le=65535)
    username: Optional[str] = Field(default=None, min_length=1, max_length=128)
    password: Optional[str] = Field(default=None, max_length=512)
    is_active: Optional[bool] = None


class DeviceResponse(DeviceBase):
    """Le mot de passe (même chiffré) n'est jamais renvoyé."""
    id: str
    category: str
    is_active: bool
    is_archived: bool = False
    last_sync_status: str
    last_sync_at: Optional[datetime] = None
    last_sync_error: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class SystemSettingResponse(BaseModel):
    key: str
    value: str
    description: Optional[str] = None
    category: str = "GENERAL"

    model_config = ConfigDict(from_attributes=True)


class SystemSettingUpdate(BaseModel):
    value: str = Field(max_length=1024)
