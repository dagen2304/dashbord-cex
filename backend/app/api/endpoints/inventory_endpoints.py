"""Inventaire des équipements supervisés, détection automatique, paramètres système."""
import ipaddress
import logging
import re
import socket
from datetime import datetime
from typing import List, Optional

import requests
import urllib3
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.redis_client import NEXT_POLL_KEY, r_client
from app.core.security import audit, require_admin
from app.models.inventory import InfrastructureDevice, ServiceDiscovery, SystemSetting
from app.models.user import User
from app.schemas.inventory import (
    DeviceCreate, DeviceDiscoveryInput, DeviceDiscoveryResponse, DeviceResponse, DeviceUpdate,
    SystemSettingResponse, SystemSettingUpdate, TlsInspectInput,
)
from app.services.tls import inspect_ssh, inspect_tls, normalize_fingerprint
from app.services.settings_service import ensure_defaults

logger = logging.getLogger(__name__)
router = APIRouter()

VENDOR_METADATA = {
    "VCENTER": {"port": 443, "category": "VIRTUALIZATION", "label": "VMware vCenter Server"},
    "DELL_UNITY": {"port": 443, "category": "STORAGE", "label": "Dell Unity"},
    "HUAWEI_DORADO": {"port": 8088, "category": "STORAGE", "label": "Huawei OceanStor Dorado"},
    "DELL_POWERSTORE": {"port": 443, "category": "STORAGE", "label": "Dell PowerStore"},
    "DATADOMAIN": {"port": 3009, "category": "BACKUP", "label": "Dell DataDomain"},
    "NETBACKUP": {"port": 1556, "category": "BACKUP", "label": "Veritas NetBackup"},
    "SCALITY": {"port": 443, "category": "OBJECT", "label": "Scality RING"},
    "VERITAS_ACCESS": {"port": 22, "category": "STORAGE", "label": "Veritas Access (SSH)"},
}

_HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")


def _validate_host(host: str) -> str:
    """Nom d'hôte ou IP uniquement (pas d'URL, de chemin ni de port)."""
    host = host.strip()
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    if not _HOST_RE.match(host):
        raise HTTPException(status_code=400, detail="Adresse invalide : indiquez une IP ou un nom d'hôte.")
    return host


def _validate_device_type(device_type: str) -> dict:
    meta = VENDOR_METADATA.get(device_type)
    if not meta:
        raise HTTPException(status_code=400, detail=f"Type d'équipement inconnu : {device_type}")
    return meta


SSH_TYPES = {"VERITAS_ACCESS"}  # équipements collectés en SSH (clé d'hôte au lieu d'un certificat)


def _tls_settings(mode: Optional[str], fingerprint: Optional[str]) -> tuple[str, Optional[str]]:
    try:
        fp = normalize_fingerprint(fingerprint)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if mode == "pin" and not fp:
        raise HTTPException(status_code=400, detail="Le mode « empreinte épinglée » exige l'empreinte SHA-256 du certificat.")
    return mode or "verify", fp


@router.get("/vendors")
def list_vendors():
    """Types d'équipements supportés (pour les formulaires)."""
    return [{"device_type": k, **v} for k, v in VENDOR_METADATA.items()]


# ── Détection automatique ───────────────────────────────────────────────────
def _port_open(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _http_probe(url: str) -> Optional[requests.Response]:
    try:
        with urllib3.warnings.catch_warnings():
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            # Sonde d'identification seulement : aucune donnée sensible n'est envoyée.
            return requests.get(url, timeout=3, verify=False, allow_redirects=False)
    except requests.RequestException:
        return None


@router.post("/discover", response_model=DeviceDiscoveryResponse)
def discover_device_signature(payload: DeviceDiscoveryInput, admin: User = Depends(require_admin),
                              db: Session = Depends(get_db)):
    """Identifie le type d'un équipement à partir de ses ports et de ses API.
    Réservé aux administrateurs. Aucune devinette sur le nom ou l'IP : si la signature
    n'est pas reconnue, l'administrateur choisit le type à la main."""
    host = _validate_host(payload.hostname_ip)
    audit(db, admin, "device.discover", host)
    db.commit()

    if _port_open(host, 8088):
        res = _http_probe(f"https://{host}:8088/deviceManager/rest/")
        if res is not None:
            return DeviceDiscoveryResponse(detected_type="HUAWEI_DORADO", recommended_port=8088, category="STORAGE", status="DETECTED")

    if _port_open(host, 443):
        res = _http_probe(f"https://{host}/sdk/vimServiceVersions.xml")
        if res is not None and res.status_code == 200 and "urn:vim25" in res.text:
            return DeviceDiscoveryResponse(detected_type="VCENTER", recommended_port=443, category="VIRTUALIZATION", status="DETECTED")
        res = _http_probe(f"https://{host}/api/types/basicSystemInfo/instances")
        if res is not None and res.status_code == 200 and "basicSystemInfo" in res.text:
            return DeviceDiscoveryResponse(detected_type="DELL_UNITY", recommended_port=443, category="STORAGE", status="DETECTED")
        res = _http_probe(f"https://{host}/api/rest/cluster")
        if res is not None and res.status_code in (200, 401) and "powerstore" in (res.headers.get("Server", "") + res.text).lower():
            return DeviceDiscoveryResponse(detected_type="DELL_POWERSTORE", recommended_port=443, category="STORAGE", status="DETECTED")

    if _port_open(host, 3009):
        return DeviceDiscoveryResponse(detected_type="DATADOMAIN", recommended_port=3009, category="BACKUP", status="DETECTED")
    if _port_open(host, 1556):
        return DeviceDiscoveryResponse(detected_type="NETBACKUP", recommended_port=1556, category="BACKUP", status="DETECTED")

    raise HTTPException(
        status_code=422,
        detail="Signature non reconnue. Choisissez le type d'équipement manuellement.",
    )


@router.post("/tls/inspect")
def inspect_device_identity(payload: TlsInspectInput, admin: User = Depends(require_admin),
                            db: Session = Depends(get_db)):
    """Lit le certificat TLS (ou la clé d'hôte SSH) présenté par l'équipement, sans s'authentifier,
    pour que l'administrateur puisse l'épingler après l'avoir comparé à la console constructeur."""
    host = _validate_host(payload.hostname_ip)
    _validate_device_type(payload.device_type)
    audit(db, admin, "device.tls_inspect", f"{host}:{payload.port}")
    db.commit()
    try:
        if payload.device_type in SSH_TYPES:
            return inspect_ssh(host, payload.port)
        return inspect_tls(host, payload.port)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"Lecture impossible sur {host}:{payload.port} — {type(exc).__name__}: {exc}")
    except Exception as exc:  # erreurs paramiko / cryptography
        raise HTTPException(status_code=502, detail=f"Lecture impossible sur {host}:{payload.port} — {type(exc).__name__}: {exc}")


# ── CRUD équipements ────────────────────────────────────────────────────────
@router.get("/devices", response_model=List[DeviceResponse])
def get_inventory_devices(
    include_archived: bool = Query(False),
    db: Session = Depends(get_db),
):
    q = db.query(InfrastructureDevice)
    if not include_archived:
        q = q.filter(InfrastructureDevice.is_archived.is_(False))
    return q.order_by(InfrastructureDevice.category, InfrastructureDevice.display_name).all()


def _sync_discovery(db: Session, device: InfrastructureDevice) -> None:
    discovery = db.query(ServiceDiscovery).filter(ServiceDiscovery.device_id == device.id).first()
    active = bool(device.is_active and not device.is_archived)
    if discovery:
        discovery.is_active = active
    else:
        db.add(ServiceDiscovery(device_id=device.id, is_active=active))


def _poll_soon(device_id: str) -> None:
    """Déclenche une collecte au prochain passage du planificateur."""
    try:
        r_client.delete(NEXT_POLL_KEY.format(device_id=device_id))
    except Exception:
        pass


@router.post("/devices", response_model=DeviceResponse, status_code=status.HTTP_201_CREATED)
def register_new_device(payload: DeviceCreate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    host = _validate_host(payload.hostname_ip)
    meta = _validate_device_type(payload.device_type)
    if not payload.password:
        raise HTTPException(status_code=400, detail="Le mot de passe du compte de service est obligatoire.")

    device = db.query(InfrastructureDevice).filter(InfrastructureDevice.hostname_ip == host).first()
    if device and not device.is_archived:
        raise HTTPException(status_code=400, detail="Un équipement actif avec cette adresse est déjà enregistré.")
    action = "device.reactivated" if device else "device.created"
    if not device:
        device = InfrastructureDevice(hostname_ip=host)
        db.add(device)

    device.display_name = payload.display_name.strip()
    device.device_type = payload.device_type
    device.category = meta["category"]  # dérivée du type : jamais incohérente
    device.port = payload.port
    device.username = payload.username
    device.set_password(payload.password)
    device.tls_mode, device.tls_fingerprint = _tls_settings(payload.tls_mode, payload.tls_fingerprint)
    if payload.device_type in SSH_TYPES and device.tls_mode == "verify":
        # Pas d'autorité de certification en SSH : la vérification passe par l'empreinte de la clé d'hôte.
        if not device.tls_fingerprint:
            raise HTTPException(status_code=400, detail="SSH : lisez la clé d'hôte et épinglez son empreinte.")
        device.tls_mode = "pin"
    device.is_active = True
    device.is_archived = False
    device.last_sync_status = "UNSYNCED"  # aucun statut tant que la première collecte n'a pas eu lieu
    device.last_sync_at = None
    device.last_sync_error = None
    db.flush()
    _sync_discovery(db, device)
    audit(db, admin, action, f"{device.display_name} ({host})", {"type": device.device_type, "tls_mode": device.tls_mode})
    db.commit()
    db.refresh(device)
    _poll_soon(device.id)
    return device


@router.put("/devices/{device_id}", response_model=DeviceResponse)
def update_device(device_id: str, payload: DeviceUpdate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    device = db.get(InfrastructureDevice, device_id)
    if not device or device.is_archived:
        raise HTTPException(status_code=404, detail="Équipement non trouvé.")

    changes = payload.model_dump(exclude_unset=True)
    password = changes.pop("password", None)
    changes.pop("category", None)  # recalculée à partir du type
    if "hostname_ip" in changes:
        changes["hostname_ip"] = _validate_host(changes["hostname_ip"])
        clash = db.query(InfrastructureDevice).filter(
            InfrastructureDevice.hostname_ip == changes["hostname_ip"], InfrastructureDevice.id != device_id
        ).first()
        if clash:
            raise HTTPException(status_code=400, detail="Cette adresse est déjà utilisée par un autre équipement.")
    if "device_type" in changes:
        device.category = _validate_device_type(changes["device_type"])["category"]

    if "tls_mode" in changes or "tls_fingerprint" in changes:
        mode = changes.pop("tls_mode", None) or device.tls_mode
        fp = changes.pop("tls_fingerprint") if "tls_fingerprint" in changes else device.tls_fingerprint
        device.tls_mode, device.tls_fingerprint = _tls_settings(mode, fp)
        if (changes.get("device_type") or device.device_type) in SSH_TYPES and device.tls_mode == "verify":
            if not device.tls_fingerprint:
                raise HTTPException(status_code=400, detail="SSH : lisez la clé d'hôte et épinglez son empreinte.")
            device.tls_mode = "pin"
        changes["tls_mode"], changes["tls_fingerprint"] = device.tls_mode, device.tls_fingerprint

    for key, value in changes.items():
        setattr(device, key, value)
    if password:
        device.set_password(password)
    _sync_discovery(db, device)
    audit(db, admin, "device.updated", device.display_name,
          {**{k: v for k, v in changes.items()}, **({"password": "changed"} if password else {})})
    db.commit()
    db.refresh(device)
    if password or {"hostname_ip", "port", "username", "device_type", "is_active", "tls_mode", "tls_fingerprint"} & changes.keys():
        _poll_soon(device.id)
    return device


@router.delete("/devices/{device_id}")
def delete_device(device_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Archivage (suppression logique) : la collecte s'arrête, l'historique est conservé."""
    device = db.get(InfrastructureDevice, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Équipement non trouvé.")
    device.is_active = False
    device.is_archived = True
    device.last_sync_status = "ARCHIVED"
    device.last_sync_at = datetime.utcnow()
    _sync_discovery(db, device)
    audit(db, admin, "device.archived", device.display_name)
    db.commit()
    return {"status": "archived", "device_id": device_id}


# ── Paramètres système ──────────────────────────────────────────────────────
_EMAIL_RE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")


def _validate_setting(key: str, val: str) -> str:
    def _int(lo: int, hi: int, label: str) -> str:
        try:
            n = int(val)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"{label} doit être un nombre entier.")
        if not lo <= n <= hi:
            raise HTTPException(status_code=400, detail=f"{label} doit être compris entre {lo} et {hi}.")
        return str(n)

    if key == "polling_interval":
        return _int(30, 3600, "L'intervalle de collecte")
    if key == "smtp_port":
        return _int(1, 65535, "Le port SMTP")
    if key == "latency_threshold_ms":
        try:
            f = float(val)
        except ValueError:
            raise HTTPException(status_code=400, detail="Le seuil doit être un nombre.")
        if not 0 < f <= 10_000:
            raise HTTPException(status_code=400, detail="Le seuil doit être compris entre 0 et 10 000 ms.")
        return str(f)
    if key == "smtp_use_tls":
        if val.lower() not in ("true", "false"):
            raise HTTPException(status_code=400, detail="Valeur attendue : true ou false.")
        return val.lower()
    if key == "alert_email_to":
        emails = [e.strip() for e in val.split(",") if e.strip()]
        bad = [e for e in emails if not _EMAIL_RE.match(e)]
        if bad:
            raise HTTPException(status_code=400, detail=f"Adresse(s) invalide(s) : {', '.join(bad)}")
        return ", ".join(emails)
    if key == "alert_email_from":
        if val and not _EMAIL_RE.match(val):
            raise HTTPException(status_code=400, detail="Adresse d'expéditeur invalide.")
        return val
    if key == "smtp_server":
        return _validate_host(val)
    return val


@router.get("/settings", response_model=List[SystemSettingResponse])
def get_system_settings(db: Session = Depends(get_db)):
    return ensure_defaults(db)


@router.put("/settings/{key}", response_model=SystemSettingResponse)
def update_system_setting(key: str, payload: SystemSettingUpdate, admin: User = Depends(require_admin),
                          db: Session = Depends(get_db)):
    ensure_defaults(db)
    setting = db.get(SystemSetting, key)
    if not setting:
        raise HTTPException(status_code=404, detail="Paramètre non trouvé.")
    new_value = _validate_setting(key, payload.value.strip())
    audit(db, admin, "setting.updated", key, {"old": setting.value, "new": new_value})
    setting.value = new_value
    db.commit()
    db.refresh(setting)
    return setting
