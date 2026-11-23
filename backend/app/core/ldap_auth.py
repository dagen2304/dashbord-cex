"""
Authentification Active Directory (LDAP).

1. Le compte de service (LDAP_BIND_DN) recherche l'utilisateur par son identifiant Windows
   (sAMAccountName), échappé contre l'injection LDAP.
2. Le mot de passe est vérifié par un bind LDAP au nom de l'utilisateur. Il n'est jamais stocké.
3. Le rôle est déduit des groupes AD configurés, groupes imbriqués compris
   (règle AD LDAP_MATCHING_RULE_IN_CHAIN), avec repli sur l'attribut memberOf direct.
   Un utilisateur qui n'appartient à aucun groupe configuré est refusé.

La connexion est toujours chiffrée (ldaps:// ou StartTLS) et le certificat du contrôleur
de domaine est vérifié (LDAP_CA_FILE pour une AC interne).
"""
import logging
import ssl
from dataclasses import dataclass
from typing import Callable, List, Optional

from ldap3 import BASE, SUBTREE, Connection, Server, ServerPool, Tls
from ldap3.core.exceptions import LDAPException
from ldap3.utils.conv import escape_filter_chars

from app.core.config import settings

logger = logging.getLogger(__name__)

IN_CHAIN = "1.2.840.113556.1.4.1941"  # LDAP_MATCHING_RULE_IN_CHAIN (appartenance imbriquée, AD)
ROLE_ORDER = ("admin", "operator", "viewer")


class LdapUnavailable(Exception):
    """Annuaire injoignable ou mal configuré (≠ identifiants invalides)."""


class LdapDenied(Exception):
    """Identifiants valides mais aucun groupe autorisé."""


@dataclass
class LdapIdentity:
    username: str
    dn: str
    full_name: Optional[str]
    role: str


def _server_pool() -> ServerPool:
    tls = Tls(validate=ssl.CERT_REQUIRED, ca_certs_file=settings.ldap_ca_file or None,
              version=ssl.PROTOCOL_TLS_CLIENT)
    servers = []
    for url in [u.strip() for u in settings.ldap_url.split(",") if u.strip()]:
        servers.append(Server(url, use_ssl=url.startswith("ldaps://"), tls=tls,
                              connect_timeout=settings.ldap_timeout_s, get_info=None))
    return ServerPool(servers, pool_strategy="FIRST", active=True, exhaust=True)


def _connect(user: Optional[str], password: Optional[str]) -> Connection:
    conn = Connection(_server_pool(), user=user, password=password, receive_timeout=settings.ldap_timeout_s,
                      raise_exceptions=False, auto_referrals=False, read_only=True)
    if settings.ldap_start_tls:
        conn.open()
        conn.start_tls()
    return conn


# Point d'injection pour les tests (annuaire simulé).
connection_factory: Callable[[Optional[str], Optional[str]], Connection] = _connect


def _role_groups() -> List[tuple[str, str]]:
    pairs = [("admin", settings.ldap_group_admin), ("operator", settings.ldap_group_operator),
             ("viewer", settings.ldap_group_viewer)]
    return [(role, dn.strip()) for role, dn in pairs if dn and dn.strip()]


def _resolve_role(conn: Connection, user_dn: str, member_of: List[str]) -> Optional[str]:
    direct = {g.lower() for g in member_of}
    for role, group_dn in _role_groups():  # du rôle le plus élevé au plus faible
        if group_dn.lower() in direct:
            return role
        try:
            conn.search(user_dn, f"(memberOf:{IN_CHAIN}:={escape_filter_chars(group_dn)})", search_scope=BASE,
                        attributes=["distinguishedName"])
            if conn.entries:
                return role
        except LDAPException as exc:
            logger.debug("Recherche imbriquée impossible pour %s : %s", group_dn, exc)
    return None


def authenticate(username: str, password: str) -> Optional[LdapIdentity]:
    """Renvoie l'identité AD, None si identifiants invalides.
    Lève LdapDenied (aucun groupe autorisé) ou LdapUnavailable (annuaire injoignable)."""
    problem = settings.ldap_problem()
    if problem:
        raise LdapUnavailable(problem)
    if not settings.ldap_enabled:
        return None
    # Un mot de passe vide ferait un « bind anonyme » que l'AD accepte : refus explicite.
    if not username or not password or not password.strip():
        return None
    if len(username) > 64 or any(c in username for c in "\\/@,=+<>#;\"*()\x00"):
        return None  # identifiant Windows simple uniquement (pas de DN ni d'UPN)

    try:
        svc = connection_factory(settings.ldap_bind_dn or None, settings.ldap_bind_password or None)
        if not svc.bind():
            raise LdapUnavailable(f"Bind du compte de service refusé : {svc.result.get('description')}")
        svc.search(settings.ldap_base_dn, settings.ldap_user_filter.format(username=escape_filter_chars(username)),
                   search_scope=SUBTREE, attributes=["distinguishedName", "displayName", "memberOf", "sAMAccountName"],
                   size_limit=2)
        if len(svc.entries) != 1:
            svc.unbind()
            return None  # inconnu (ou ambigu) : même réponse qu'un mauvais mot de passe
        entry = svc.entries[0]
        user_dn = entry.entry_dn
        member_of = [str(g) for g in entry.memberOf.values] if "memberOf" in entry else []
        full_name = str(entry.displayName.value) if "displayName" in entry and entry.displayName.value else None

        user_conn = connection_factory(user_dn, password)
        ok = user_conn.bind()
        user_conn.unbind()
        if not ok:
            svc.unbind()
            return None

        role = _resolve_role(svc, user_dn, member_of)
        svc.unbind()
    except LDAPException as exc:
        raise LdapUnavailable(f"{type(exc).__name__}: {exc}") from exc

    if not role:
        raise LdapDenied(username)
    return LdapIdentity(username=username, dn=user_dn, full_name=full_name, role=role)
