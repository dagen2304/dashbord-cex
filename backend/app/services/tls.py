"""
Politique de vérification des connexions aux équipements (TLS et SSH).

Trois modes, réglés par équipement dans l'inventaire :

- « verify »   : certificat vérifié par une autorité de certification (celles du système,
                 ou le fichier DEVICE_CA_BUNDLE pour une AC interne) ET nom d'hôte vérifié.
- « pin »      : empreinte SHA-256 du certificat épinglée. Adapté aux certificats
                 auto-signés des baies et aux équipements joints par adresse IP.
                 Pour SSH (Veritas Access), c'est l'empreinte de la clé d'hôte.
- « insecure » : aucune vérification (comportement de la v2). Conservé uniquement pour
                 ne pas casser les équipements existants à la mise à jour ; signalé
                 dans l'interface et dans les journaux à chaque collecte.
"""
import hashlib
import logging
import os
import socket
import ssl
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import requests
import urllib3
from requests.adapters import HTTPAdapter

logger = logging.getLogger(__name__)

TLS_MODES = ("verify", "pin", "insecure")

# Les avertissements urllib3 seraient émis à chaque requête : on journalise plutôt une fois
# par collecte (mode « insecure »), et le mode « pin » vérifie l'empreinte lui-même.
warnings.filterwarnings("ignore", category=urllib3.exceptions.InsecureRequestWarning)


def normalize_fingerprint(value: Optional[str]) -> Optional[str]:
    """« AB:CD:… » ou « abcd… » → 64 caractères hexadécimaux minuscules ; None si vide."""
    if not value:
        return None
    fp = value.replace(":", "").replace(" ", "").strip().lower()
    if len(fp) != 64 or any(c not in "0123456789abcdef" for c in fp):
        raise ValueError("Empreinte SHA-256 invalide : 64 caractères hexadécimaux attendus.")
    return fp


def ca_bundle() -> Optional[str]:
    path = os.getenv("DEVICE_CA_BUNDLE") or None
    if path and not os.path.isfile(path):
        logger.error("DEVICE_CA_BUNDLE=%s introuvable : AC système utilisées.", path)
        return None
    return path


@dataclass(frozen=True)
class TlsPolicy:
    mode: str = "verify"
    fingerprint: Optional[str] = None  # SHA-256 hexadécimal

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "TlsPolicy":
        mode = config.get("tls_mode") or "verify"
        if mode not in TLS_MODES:
            raise ValueError(f"Mode TLS inconnu : {mode}")
        return cls(mode=mode, fingerprint=normalize_fingerprint(config.get("tls_fingerprint")))

    def check(self) -> None:
        if self.mode == "pin" and not self.fingerprint:
            raise ValueError("Mode « empreinte épinglée » sans empreinte : lisez le certificat depuis l'inventaire.")


# ── HTTPS (requests) ────────────────────────────────────────────────────────
class _PinnedAdapter(HTTPAdapter):
    """Accepte uniquement le certificat dont l'empreinte SHA-256 est épinglée."""

    def __init__(self, fingerprint: str, **kwargs):
        self._fingerprint = fingerprint
        super().__init__(**kwargs)

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        pool_kwargs["assert_fingerprint"] = self._fingerprint
        super().init_poolmanager(connections, maxsize, block=block, **pool_kwargs)


def http_session(policy: TlsPolicy, device_name: str = "") -> requests.Session:
    policy.check()
    session = requests.Session()
    session.trust_env = False  # pas de proxy système implicite vers les équipements
    if policy.mode == "verify":
        session.verify = ca_bundle() or True
    elif policy.mode == "pin":
        # La chaîne n'est pas validée par une AC : c'est l'empreinte exacte qui fait foi.
        session.verify = False
        session.mount("https://", _PinnedAdapter(policy.fingerprint))
    else:
        session.verify = False
        logger.warning("[TLS] %s : certificat NON vérifié (mode « insecure »).", device_name or "équipement")
    return session


# ── vSphere (pyVmomi) ───────────────────────────────────────────────────────
def vsphere_connect_kwargs(policy: TlsPolicy, device_name: str = "") -> Dict[str, Any]:
    """Arguments sslContext / thumbprint pour pyVim.connect.SmartConnect.
    En mode « pin », pyVmomi tente d'abord la vérification par AC puis, en cas d'échec,
    compare le certificat à l'empreinte épinglée."""
    policy.check()
    if policy.mode == "insecure":
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        logger.warning("[TLS] %s : certificat NON vérifié (mode « insecure »).", device_name or "vCenter")
        return {"sslContext": ctx}
    ctx = ssl.create_default_context(cafile=ca_bundle())
    if policy.mode == "pin":
        return {"sslContext": ctx, "thumbprint": policy.fingerprint}
    return {"sslContext": ctx}


# ── SSH (paramiko) ──────────────────────────────────────────────────────────
def ssh_key_fingerprint(key) -> str:
    return hashlib.sha256(key.asbytes()).hexdigest()


def ssh_host_key_policy(policy: TlsPolicy, device_name: str = ""):
    """Politique de clé d'hôte paramiko. Pas d'AC en SSH : « verify » exige une empreinte."""
    import paramiko

    if policy.mode == "insecure":
        logger.warning("[SSH] %s : clé d'hôte NON vérifiée (mode « insecure »).", device_name or "équipement")
        return paramiko.AutoAddPolicy()
    if not policy.fingerprint:
        raise ValueError("SSH : épinglez l'empreinte de la clé d'hôte depuis l'inventaire (lire le certificat).")

    expected = policy.fingerprint

    class _Pinned(paramiko.MissingHostKeyPolicy):
        def missing_host_key(self, client, hostname, key):
            actual = ssh_key_fingerprint(key)
            if actual != expected:
                raise paramiko.SSHException(
                    f"Clé d'hôte SSH inattendue pour {hostname} (SHA-256 {actual}) : connexion refusée.")

    return _Pinned()


# ── Lecture d'un certificat (inventaire) ───────────────────────────────────
def _name(parts) -> str:
    return ", ".join(f"{k}={v}" for rdn in parts or () for k, v in rdn)


def inspect_tls(host: str, port: int, timeout: float = 8.0) -> Dict[str, Any]:
    """Lit le certificat présenté (sans rien envoyer d'autre) et indique s'il serait accepté
    en mode « verify ». Sert à épingler son empreinte en connaissance de cause."""
    from cryptography import x509

    raw = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    raw.check_hostname = False
    raw.verify_mode = ssl.CERT_NONE  # lecture seule du certificat, pour l'afficher à l'administrateur
    with socket.create_connection((host, port), timeout=timeout) as sock:
        with raw.wrap_socket(sock, server_hostname=host) as tls:
            der = tls.getpeercert(binary_form=True)
    cert = x509.load_der_x509_certificate(der)
    try:
        sans = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        san_list = [str(v) for v in sans.get_values_for_type(x509.DNSName)] + \
                   [str(v) for v in sans.get_values_for_type(x509.IPAddress)]
    except x509.ExtensionNotFound:
        san_list = []

    trusted, reason = True, None
    try:
        ctx = ssl.create_default_context(cafile=ca_bundle())
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host):
                pass
    except ssl.SSLCertVerificationError as exc:
        trusted, reason = False, exc.verify_message or str(exc)
    except (ssl.SSLError, OSError) as exc:
        trusted, reason = False, str(exc)

    not_after = cert.not_valid_after_utc
    return {
        "kind": "tls",
        "subject": cert.subject.rfc4514_string(),
        "issuer": cert.issuer.rfc4514_string(),
        "self_signed": cert.subject == cert.issuer,
        "subject_alt_names": san_list,
        "not_before": cert.not_valid_before_utc.isoformat(),
        "not_after": not_after.isoformat(),
        "expired": not_after < datetime.now(timezone.utc),
        "sha256": hashlib.sha256(der).hexdigest(),
        "trusted": trusted,
        "trust_error": reason,
    }


def inspect_ssh(host: str, port: int, timeout: float = 8.0) -> Dict[str, Any]:
    """Lit la clé d'hôte SSH (sans s'authentifier)."""
    import paramiko

    sock = socket.create_connection((host, port), timeout=timeout)
    transport = paramiko.Transport(sock)
    try:
        transport.start_client(timeout=timeout)
        key = transport.get_remote_server_key()
        return {"kind": "ssh", "key_type": key.get_name(), "sha256": ssh_key_fingerprint(key),
                "trusted": False, "trust_error": "Pas d'autorité de certification en SSH : épinglez l'empreinte."}
    finally:
        transport.close()
