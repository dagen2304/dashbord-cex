import logging
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.redis_client import r_client
from app.core.security import (
    audit, client_ip, create_access_token, get_current_user, hash_password,
    require_admin, validate_password_strength, verify_password,
)
from app.models.user import AuditEvent, User
from app.schemas.auth import (
    AuditEventOut, LoginRequest, PasswordChange, TokenResponse, UserCreate, UserOut, UserUpdate,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# Hash factice : vérifier un mot de passe même si l'utilisateur n'existe pas,
# pour ne pas révéler par le temps de réponse quels comptes existent.
_DUMMY_HASH = hash_password("dummy-password-for-timing")


def _fail_key(username: str, ip: str) -> str:
    return f"noc_login_fail:{username.lower()}:{ip}"


def _is_locked(key: str) -> bool:
    try:
        count = r_client.get(key)
        return count is not None and int(count) >= settings.login_max_attempts
    except Exception:  # Redis indisponible : on ne bloque pas la connexion
        return False


def _register_failure(key: str) -> None:
    try:
        pipe = r_client.pipeline()
        pipe.incr(key)
        pipe.expire(key, settings.login_lock_minutes * 60)
        pipe.execute()
    except Exception:
        pass


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    ip = client_ip(request)
    key = _fail_key(payload.username, ip)
    if _is_locked(key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Trop de tentatives. Réessayez dans {settings.login_lock_minutes} minutes.",
        )

    user = db.query(User).filter(User.username == payload.username).first()
    valid = verify_password(payload.password, user.password_hash if user else _DUMMY_HASH)
    if not user or not valid or not user.is_active:
        _register_failure(key)
        audit(db, payload.username[:64], "auth.login_failed", details={"ip": ip})
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Identifiant ou mot de passe incorrect.")

    try:
        r_client.delete(key)
    except Exception:
        pass
    user.last_login_at = datetime.utcnow()
    audit(db, user, "auth.login", details={"ip": ip})
    db.commit()
    db.refresh(user)
    token, expires_in = create_access_token(user)
    return TokenResponse(access_token=token, expires_in=expires_in, user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


@router.post("/me/password", status_code=status.HTTP_204_NO_CONTENT)
def change_my_password(payload: PasswordChange, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Mot de passe actuel incorrect.")
    validate_password_strength(payload.new_password)
    user.password_hash = hash_password(payload.new_password)
    user.token_version += 1  # déconnecte les autres sessions
    audit(db, user, "user.password_changed", user.username)
    db.commit()


# ── Gestion des utilisateurs (admin) ────────────────────────────────────────
@router.get("/users", response_model=List[UserOut])
def list_users(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    return db.query(User).order_by(User.username).all()


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(payload: UserCreate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == payload.username).first():
        raise HTTPException(status_code=400, detail="Cet identifiant existe déjà.")
    validate_password_strength(payload.password)
    user = User(
        username=payload.username,
        full_name=payload.full_name,
        password_hash=hash_password(payload.password),
        role=payload.role,
    )
    db.add(user)
    audit(db, admin, "user.created", payload.username, {"role": payload.role})
    db.commit()
    db.refresh(user)
    return user


@router.put("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, payload: UserUpdate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
    changes = payload.model_dump(exclude_unset=True)

    # Ne jamais laisser la plateforme sans administrateur actif.
    demoting = ("role" in changes and changes["role"] != "admin") or changes.get("is_active") is False
    if user.role == "admin" and demoting:
        other_admins = db.query(User).filter(User.role == "admin", User.is_active.is_(True), User.id != user.id).count()
        if other_admins == 0:
            raise HTTPException(status_code=400, detail="Impossible : ce compte est le dernier administrateur actif.")

    if changes.get("password"):
        validate_password_strength(changes["password"])
        user.password_hash = hash_password(changes["password"])
        user.token_version += 1
    if "full_name" in changes:
        user.full_name = changes["full_name"]
    if changes.get("role"):
        user.role = changes["role"]
        user.token_version += 1
    if "is_active" in changes and changes["is_active"] is not None:
        user.is_active = changes["is_active"]
        user.token_version += 1

    audit(db, admin, "user.updated", user.username, {k: v for k, v in changes.items() if k != "password"} | (
        {"password": "changed"} if changes.get("password") else {}))
    db.commit()
    db.refresh(user)
    return user


@router.get("/audit", response_model=List[AuditEventOut])
def list_audit_events(
    limit: int = Query(200, ge=1, le=1000),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return db.query(AuditEvent).order_by(AuditEvent.timestamp.desc()).limit(limit).all()
