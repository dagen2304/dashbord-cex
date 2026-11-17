"""
Configuration unique de l'application (pydantic-settings).

Toutes les variables d'environnement sont lues ici et nulle part ailleurs.
L'application refuse de démarrer si un secret est absent ou trivial.
"""
from functools import lru_cache
from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_WEAK_SECRETS = {
    "noc_super_secret_encryption_key_12345",
    "generate_a_very_secret_key_for_jwt_auth",
    "noc_super_secret_token_12345",
    "noc_secure_password",
}


def _check_secret(name: str, value: str) -> str:
    if not value or len(value) < 32 or value in _WEAK_SECRETS or value.upper().startswith("CHANGE_ME"):
        raise ValueError(
            f"{name} absente ou faible : générez-la avec "
            "python -c \"import secrets; print(secrets.token_urlsafe(48))\""
        )
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    # Bases de données
    database_url: str
    redis_url: str = "redis://redis:6379/0"
    influxdb_url: str = "http://influxdb:8086"
    influxdb_token: str
    influxdb_org: str = "noc_org"
    influxdb_bucket: str = "noc_metrics"

    # Secrets
    # SECRET_KEY : chiffre les mots de passe des équipements en base (Fernet).
    secret_key: str
    # JWT_SECRET : signe les jetons de session. Doit être différent de SECRET_KEY.
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 480  # durée d'une session (une vacation NOC)

    # Compte administrateur créé au premier démarrage si la table users est vide.
    admin_username: str = "admin"
    admin_initial_password: str = ""

    # Web
    cors_origins: str = ""  # liste séparée par des virgules ; vide = même origine uniquement

    # Mode démonstration (app/demo) : équipements fictifs *.demo.invalid. Jamais en production.
    demo_data: bool = False

    # Collecte
    collector_timeout_s: int = 120  # durée max d'une collecte d'un équipement
    login_max_attempts: int = 5
    login_lock_minutes: int = 15

    @field_validator("secret_key")
    @classmethod
    def _secret_key(cls, v: str) -> str:
        return _check_secret("SECRET_KEY", v)

    @field_validator("jwt_secret")
    @classmethod
    def _jwt_secret(cls, v: str, info) -> str:
        _check_secret("JWT_SECRET", v)
        if v == info.data.get("secret_key"):
            raise ValueError("JWT_SECRET doit être différent de SECRET_KEY")
        return v

    @property
    def cors_origin_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
