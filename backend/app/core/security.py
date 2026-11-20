"""
Authentification (JWT) et autorisation (rôles) de l'API.

- Mots de passe hachés avec bcrypt.
- Jeton d'accès JWT signé avec JWT_SECRET (distinct de SECRET_KEY).
- Le jeton embarque `ver` (token_version) : changer le mot de passe ou désactiver
  un compte invalide immédiatement les sessions existantes.
"""
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db, SessionLocal
from app.models.user import AuditEvent, ROLES, User

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)
_ROLE_RANK = {role: i for i, role in enumerate(ROLES)}


# ── Mots de passe ───────────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8")[:72], bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:72], password_hash.encode())
    except ValueError:
        return False


def validate_password_strength(password: str) -> None:
    if len(password) < 12:
        raise HTTPException(status_code=400, detail="Le mot de passe doit contenir au moins 12 caractères.")
    classes = sum([
        any(c.islower() for c in password),
        any(c.isupper() for c in password),
        any(c.isdigit() for c in password),
        any(not c.isalnum() for c in password),
    ])
    if classes < 3:
        raise HTTPException(
            status_code=400,
            detail="Le mot de passe doit mélanger au moins 3 types de caractères (minuscules, majuscules, chiffres, symboles).",
        )


# ── Jetons ──────────────────────────────────────────────────────────────────
def create_access_token(user: User) -> tuple[str, int]:
    expires_in = settings.access_token_minutes * 60
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "usr": user.username,
        "role": user.role,
        "ver": user.token_version,
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm), expires_in


def user_from_token(token: str, db: Session) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Session invalide ou expirée.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise unauthorized
    user = db.get(User, user_id)
    if not user or not user.is_active or user.token_version != payload.get("ver"):
        raise unauthorized
    return user


def get_current_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    if not creds or creds.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentification requise.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user_from_token(creds.credentials, db)


def require_role(minimum: str) -> Callable[..., User]:
    """Dépendance : l'utilisateur doit avoir au moins le rôle `minimum`."""
    if minimum not in _ROLE_RANK:
        raise ValueError(minimum)

    def _checker(user: User = Depends(get_current_user)) -> User:
        if _ROLE_RANK.get(user.role, -1) < _ROLE_RANK[minimum]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Droits insuffisants pour cette action.")
        return user

    return _checker


require_operator = require_role("operator")
require_admin = require_role("admin")


# ── Audit ───────────────────────────────────────────────────────────────────
def audit(db: Session, user: User | str, action: str, target: str | None = None, details: dict | None = None) -> None:
    """Ajoute un évènement d'audit dans la transaction courante (commit par l'appelant)."""
    username = user if isinstance(user, str) else user.username
    db.add(AuditEvent(
        username=username,
        action=action,
        target=target,
        details=json.dumps(details, ensure_ascii=False, default=str) if details else None,
    ))


def client_ip(request: Request) -> str:
    # Derrière Nginx, l'IP réelle est dans X-Real-IP (positionné par notre nginx.conf).
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")


# ── Compte initial ──────────────────────────────────────────────────────────
def bootstrap_admin() -> None:
    """Crée le premier administrateur si aucun utilisateur n'existe."""
    with SessionLocal() as db:
        if db.query(User).count() > 0:
            return
        if not settings.admin_initial_password:
            logger.warning(
                "Aucun utilisateur en base et ADMIN_INITIAL_PASSWORD vide : "
                "définissez-le pour créer le premier compte administrateur."
            )
            return
        db.add(User(
            username=settings.admin_username,
            full_name="Administrateur",
            password_hash=hash_password(settings.admin_initial_password),
            role="admin",
        ))
        audit(db, "system", "user.bootstrap", settings.admin_username)
        db.commit()
        logger.info("Compte administrateur initial '%s' créé.", settings.admin_username)
