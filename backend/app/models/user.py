from datetime import datetime

from sqlalchemy import Column, String, Integer, Boolean, DateTime, Text

from app.core.database import Base

ROLES = ("viewer", "operator", "admin")


class User(Base):
    """Utilisateur du dashboard. Rôles : viewer (lecture), operator (exports, applications,
    acquittement des alertes), admin (équipements, paramètres, utilisateurs)."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(64), unique=True, index=True, nullable=False)
    full_name = Column(String(128), nullable=True)
    password_hash = Column(String(128), nullable=False)
    role = Column(String(16), nullable=False, default="viewer")
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_login_at = Column(DateTime, nullable=True)
    # « local » : mot de passe géré ici. « ldap » : compte Active Directory, mot de passe vérifié
    # par l'AD à chaque connexion (jamais stocké) et rôle synchronisé depuis ses groupes AD.
    auth_source = Column(String(16), nullable=False, default="local")
    # Incrémenté à chaque changement de mot de passe ou désactivation : invalide les jetons émis avant.
    token_version = Column(Integer, nullable=False, default=0)


class AuditEvent(Base):
    """Journal d'audit : qui a modifié quoi."""
    __tablename__ = "audit_events"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    username = Column(String(64), nullable=False, index=True)
    action = Column(String(64), nullable=False)
    target = Column(String(256), nullable=True)
    details = Column(Text, nullable=True)
