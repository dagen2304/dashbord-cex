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
from app.core import ldap_auth
from app.schemas.auth import (
    AuditEventOut, AuthConfig, LoginRequest, PasswordChange, TokenResponse, UserCreate, UserOut, UserUpdate,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# Hash factice : vérifier un mot de passe même si l'utilisateur n'existe pas,
# pour ne pas révéler par le temps de réponse quels comptes existent.
_DUMMY_HASH = hash_password("dummy-password-for-timing")


def _fail_key(username: str, ip: str) -> str:
    return f"noc_login_fail:{username.lower()}:{ip}"


def _ip_key(ip: str) -> str:
    """Échecs d'une adresse, tous identifiants confondus : bloque la « pulvérisation » de
    mots de passe (quelques essais sur de nombreux comptes, notamment des comptes AD)."""
    return f"noc_login_fail_ip:{ip}"


def _is_locked(key: str, limit: int | None = None) -> bool:
    try:
        count = r_client.get(key)
        return count is not None and int(count) >= (limit or settings.login_max_attempts)
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


def _login_failed(db: Session, key: str, username: str, ip: str, action: str = "auth.login_failed",
                  status_code: int = status.HTTP_401_UNAUTHORIZED,
                  detail: str = "Identifiant ou mot de passe incorrect.") -> HTTPException:
    _register_failure(key)
    _register_failure(_ip_key(ip))
    audit(db, username[:64], action, details={"ip": ip})
    db.commit()
    return HTTPException(status_code=status_code, detail=detail)


def _ldap_login(db: Session, payload: LoginRequest, key: str, ip: str) -> User:
    """Connexion par l'Active Directory ; crée ou met à jour le compte « ldap » correspondant."""
    try:
        identity = ldap_auth.authenticate(payload.username, payload.password)
    except ldap_auth.LdapDenied:
        raise _login_failed(db, key, payload.username, ip, "auth.ldap_denied", status.HTTP_403_FORBIDDEN,
                            "Votre compte AD n'appartient à aucun groupe autorisé pour ce dashboard.")
    except ldap_auth.LdapUnavailable as exc:
        logger.error("Annuaire AD indisponible : %s", exc)
        audit(db, payload.username[:64], "auth.ldap_unavailable", details={"ip": ip, "error": str(exc)[:200]})
        db.commit()
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Annuaire AD indisponible. Réessayez, ou utilisez un compte local de secours.")
    if identity is None:
        raise _login_failed(db, key, payload.username, ip)

    username = identity.username.lower()
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        # Valeurs explicites : les « default » de colonne ne s'appliquent qu'à l'insertion en base.
        user = User(username=username, auth_source="ldap", password_hash="!ldap", role=identity.role,
                    full_name=identity.full_name, is_active=True, token_version=0)
        db.add(user)
        audit(db, "system", "user.ldap_provisioned", username, {"role": identity.role})
    elif user.auth_source != "ldap":
        # Un compte local du même nom existe : il prime (et son mot de passe a déjà été refusé).
        raise _login_failed(db, key, payload.username, ip)
    if not user.is_active:
        raise _login_failed(db, key, payload.username, ip, detail="Compte désactivé dans le dashboard.")
    if user.role != identity.role:
        audit(db, "system", "user.ldap_role_synced", username, {"old": user.role, "new": identity.role})
        user.role = identity.role
        user.token_version += 1
    if identity.full_name:
        user.full_name = identity.full_name
    db.flush()
    return user


@router.get("/config", response_model=AuthConfig)
def auth_config():
    """Public : indique à l'écran de connexion si les comptes AD sont acceptés."""
    return AuthConfig(ldap_enabled=settings.ldap_enabled, ldap_domain_label=settings.ldap_domain_label or None)


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    ip = client_ip(request)
    key = _fail_key(payload.username, ip)
    if _is_locked(key) or _is_locked(_ip_key(ip), settings.login_max_attempts_per_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Trop de tentatives. Réessayez dans {settings.login_lock_minutes} minutes.",
        )

    # 1. Compte local (dont le compte administrateur de secours) : prioritaire s'il existe.
    local = db.query(User).filter(User.username == payload.username, User.auth_source == "local").first()
    if local is not None or not settings.ldap_enabled:
        valid = verify_password(payload.password, local.password_hash if local else _DUMMY_HASH)
        if not local or not valid or not local.is_active:
            raise _login_failed(db, key, payload.username, ip)
        user = local
    else:
        # 2. Active Directory.
        user = _ldap_login(db, payload, key, ip)

    try:
        r_client.delete(key)
    except Exception:
        pass
    user.last_login_at = datetime.utcnow()
    audit(db, user, "auth.login", details={"ip": ip, "source": user.auth_source})
    db.commit()
    db.refresh(user)
    token, expires_in = create_access_token(user)
    return TokenResponse(access_token=token, expires_in=expires_in, user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


@router.post("/me/password", status_code=status.HTTP_204_NO_CONTENT)
def change_my_password(payload: PasswordChange, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.auth_source == "ldap":
        raise HTTPException(status_code=400, detail="Compte Active Directory : changez votre mot de passe Windows.")
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
    if user.auth_source == "ldap" and ({"password", "role", "full_name"} & {k for k, v in changes.items() if v is not None}):
        raise HTTPException(status_code=400,
                            detail="Compte Active Directory : le mot de passe, le nom et le rôle viennent de l'AD (groupes). Seule l'activation se règle ici.")

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
