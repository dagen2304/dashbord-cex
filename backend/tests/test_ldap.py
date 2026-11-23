"""Connexion Active Directory — annuaire LDAP simulé en mémoire (ldap3 MOCK_SYNC)."""
import pytest
from ldap3 import MOCK_SYNC, OFFLINE_AD_2012_R2, Connection, Server

from app.core import ldap_auth
from app.core.config import settings
from app.models.user import User

BASE = "DC=adcm,DC=orangecm"
G_ADMIN = f"CN=NOC-Admins,OU=Groupes,{BASE}"
G_OPS = f"CN=NOC-Operateurs,OU=Groupes,{BASE}"
G_VIEW = f"CN=NOC-Lecteurs,OU=Groupes,{BASE}"
SVC = f"CN=svc-noc,OU=Services,{BASE}"


def _person(conn, sam, display, groups, password):
    conn.strategy.add_entry(f"CN={display},OU=Utilisateurs,{BASE}", {
        "objectClass": ["top", "person", "organizationalPerson", "user"], "objectCategory": "person",
        "sAMAccountName": sam, "displayName": display, "memberOf": groups, "userPassword": password,
    })


@pytest.fixture
def directory(monkeypatch):
    server = Server("fake-dc", get_info=OFFLINE_AD_2012_R2)
    setup = Connection(server, user=SVC, password="svc-pass", client_strategy=MOCK_SYNC)
    setup.strategy.add_entry(SVC, {"objectClass": ["top", "user"], "sAMAccountName": "svc-noc", "userPassword": "svc-pass"})
    _person(setup, "jdupont", "Jean Dupont", [G_OPS], "Windows-Pass1")
    _person(setup, "amartin", "Awa Martin", [G_VIEW, G_ADMIN], "Windows-Pass2")
    _person(setup, "intrus", "Paul Intrus", [f"CN=Compta,OU=Groupes,{BASE}"], "Windows-Pass3")

    monkeypatch.setattr(ldap_auth, "connection_factory",
                        lambda user, password: Connection(server, user=user, password=password, client_strategy=MOCK_SYNC))
    for k, v in {"ldap_enabled": True, "ldap_url": "ldaps://dc01.adcm.orangecm", "ldap_base_dn": BASE,
                 "ldap_bind_dn": SVC, "ldap_bind_password": "svc-pass", "ldap_group_admin": G_ADMIN,
                 "ldap_group_operator": G_OPS, "ldap_group_viewer": G_VIEW, "ldap_domain_label": "ADCM"}.items():
        monkeypatch.setattr(settings, k, v)
    return server


def _login(client, username, password):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def test_ad_user_is_provisioned_with_role_from_group(client, directory, db):
    r = _login(client, "jdupont", "Windows-Pass1")
    assert r.status_code == 200, r.text
    u = r.json()["user"]
    assert u["role"] == "operator" and u["auth_source"] == "ldap" and u["full_name"] == "Jean Dupont"
    row = db.query(User).filter(User.username == "jdupont").one()
    assert row.password_hash == "!ldap"  # le mot de passe Windows n'est jamais stocké


def test_highest_role_wins(client, directory):
    assert _login(client, "amartin", "Windows-Pass2").json()["user"]["role"] == "admin"


def test_wrong_password_and_unknown_user_get_the_same_answer(client, directory):
    for user, pwd in (("jdupont", "faux"), ("inconnu", "Windows-Pass1")):
        r = _login(client, user, pwd)
        assert r.status_code == 401 and r.json()["detail"] == "Identifiant ou mot de passe incorrect."


def test_user_without_allowed_group_is_refused(client, directory, db):
    r = _login(client, "intrus", "Windows-Pass3")
    assert r.status_code == 403
    assert db.query(User).filter(User.username == "intrus").first() is None


def test_empty_password_never_reaches_the_directory(client, directory, monkeypatch):
    called = []
    monkeypatch.setattr(ldap_auth, "connection_factory", lambda *a: called.append(a))
    assert _login(client, "jdupont", " ").status_code == 401
    assert called == []  # pas de « bind anonyme »


def test_ldap_injection_is_neutralised(client, directory):
    for evil in ("*", "jdupont)(sAMAccountName=*", "j*"):
        assert _login(client, evil, "Windows-Pass1").status_code == 401


def test_local_admin_still_works_and_takes_precedence(client, directory):
    assert _login(client, "admin", "Admin-Passw0rd!").status_code == 200


def test_role_is_resynced_at_each_login(client, directory, db):
    _login(client, "jdupont", "Windows-Pass1")
    monkeypatch_groups = Connection(directory, user=SVC, password="svc-pass", client_strategy=MOCK_SYNC)
    monkeypatch_groups.bind()
    monkeypatch_groups.modify(f"CN=Jean Dupont,OU=Utilisateurs,{BASE}", {"memberOf": [("MODIFY_REPLACE", [G_VIEW])]})
    assert _login(client, "jdupont", "Windows-Pass1").json()["user"]["role"] == "viewer"


def test_ad_accounts_cannot_change_password_or_role_here(client, directory, admin_headers):
    token = _login(client, "jdupont", "Windows-Pass1").json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    assert client.post("/api/auth/me/password", headers=h,
                       json={"current_password": "Windows-Pass1", "new_password": "Autre-Passw0rd!x"}).status_code == 400
    uid = client.get("/api/auth/me", headers=h).json()["id"]
    assert client.put(f"/api/auth/users/{uid}", headers=admin_headers, json={"role": "admin"}).status_code == 400
    # …mais un administrateur peut le désactiver, ce qui bloque la connexion suivante.
    assert client.put(f"/api/auth/users/{uid}", headers=admin_headers, json={"is_active": False}).status_code == 200
    assert _login(client, "jdupont", "Windows-Pass1").status_code == 401


def test_plain_ldap_without_starttls_is_refused(client, directory, monkeypatch):
    monkeypatch.setattr(settings, "ldap_url", "ldap://dc01.adcm.orangecm")
    r = _login(client, "jdupont", "Windows-Pass1")
    assert r.status_code == 503


def test_auth_config_is_public(client, directory):
    assert client.get("/api/auth/config").json() == {"ldap_enabled": True, "ldap_domain_label": "ADCM"}
