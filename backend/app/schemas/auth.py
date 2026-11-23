from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

Role = Literal["viewer", "operator", "admin"]


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class UserOut(BaseModel):
    id: int
    username: str
    full_name: Optional[str] = None
    role: Role
    is_active: bool
    auth_source: Literal["local", "ldap"] = "local"
    created_at: datetime
    last_login_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9._@-]+$")
    full_name: Optional[str] = Field(default=None, max_length=128)
    password: str = Field(max_length=256)
    role: Role = "viewer"


class UserUpdate(BaseModel):
    full_name: Optional[str] = Field(default=None, max_length=128)
    password: Optional[str] = Field(default=None, max_length=256)
    role: Optional[Role] = None
    is_active: Optional[bool] = None


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)


class AuditEventOut(BaseModel):
    id: int
    timestamp: datetime
    username: str
    action: str
    target: Optional[str] = None
    details: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class AuthConfig(BaseModel):
    """Informations publiques affichées sur l'écran de connexion."""
    ldap_enabled: bool
    ldap_domain_label: Optional[str] = None
