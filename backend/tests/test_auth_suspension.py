"""Refus d'identifiants : un seul essai, puis collecte suspendue jusqu'à action d'un administrateur.

Régression : un mot de passe refusé par une baie Huawei Dorado était retenté à chaque
collecte (toutes les 60 s), jusqu'au verrouillage du compte (« You can try for 4 times »).
"""
import json
import ssl

import requests
from requests.adapters import BaseAdapter

from app.core.redis_client import NEXT_POLL_KEY, r_client
from app.models.inventory import AUTH_FAILED, InfrastructureDevice
from app.services import base_collector
from app.services.base_collector import CredentialsRejected, is_auth_error
from app.services.datadomain_collector import DataDomainCollector
from app.services.dorado_collector import DoradoCollector
from app.tasks import scheduler

DORADO_BAD_PASSWORD = {"data": {}, "error": {"code": 1077987870, "description": "The user name or password is incorrect.",
                                             "errorParam": "4", "suggestion": "Enter the correct user name and password. You can try for 4 times."}}


class Fake(BaseAdapter):
    """Faux équipement : `routes` associe (méthode, chemin) → (code HTTP, corps JSON)."""

    def __init__(self, routes):
        super().__init__()
        self.routes, self.calls = routes, []

    def send(self, request, **kwargs):
        path = requests.utils.urlparse(request.url).path
        body = json.loads(request.body) if request.body else None
        self.calls.append((request.method, path, body))
        status, data = self.routes.get((request.method, path), (404, {}))
        resp = requests.Response()
        resp.request, resp.url, resp.status_code, resp._content = request, request.url, status, json.dumps(data).encode()
        return resp

    def close(self):
        pass


def _run(collector, adapter, monkeypatch):
    original = base_collector.BaseCollector.http_session

    def fake_session(self):
        s = original(self)
        s.mount("https://", adapter)
        return s

    monkeypatch.setattr(base_collector.BaseCollector, "http_session", fake_session)
    return collector.collect()


def _cfg(**kw):
    return {"host": "eq.example", "port": 8088, "username": "svc", "password": "faux", "tls_mode": "insecure", **kw}


# ── Classification ──────────────────────────────────────────────────────────
def test_auth_error_classification():
    resp = requests.Response(); resp.status_code = 401
    assert is_auth_error(requests.HTTPError(response=resp))
    assert is_auth_error(CredentialsRejected("x")) and is_auth_error(PermissionError("x"))
    assert is_auth_error(type("InvalidLogin", (Exception,), {})())          # pyVmomi
    assert is_auth_error(type("AuthenticationException", (Exception,), {})())  # paramiko
    resp500 = requests.Response(); resp500.status_code = 500
    for exc in (requests.HTTPError(response=resp500), ssl.SSLError("cert"), TimeoutError(), ConnectionError()):
        assert not is_auth_error(exc), exc   # réseau, TLS, panne : on continue à réessayer


# ── Dorado ──────────────────────────────────────────────────────────────────
def test_dorado_wrong_password_is_flagged_after_one_attempt(monkeypatch):
    fake = Fake({("POST", "/deviceManager/rest/v2/sessions"): (200, DORADO_BAD_PASSWORD)})
    payload = _run(DoradoCollector("d", "Dorado", _cfg()), fake, monkeypatch)
    assert payload["status"] == "OFFLINE" and payload["auth_failed"] is True
    assert "Identifiants refusés par la baie (code 1077987870)" in payload["alerts"][0]["message"]
    assert [c[0] for c in fake.calls] == ["POST"]   # un seul essai, pas de déconnexion sans session


def test_dorado_ldap_account_uses_scope_1(monkeypatch):
    fake = Fake({("POST", "/deviceManager/rest/v2/sessions"): (200, DORADO_BAD_PASSWORD)})
    _run(DoradoCollector("d", "Dorado", _cfg(username="LDAP\\svc_noc")), fake, monkeypatch)
    assert fake.calls[0][2] == {"username": "svc_noc", "password": "faux", "scope": 1}
    fake2 = Fake({("POST", "/deviceManager/rest/v2/sessions"): (200, DORADO_BAD_PASSWORD)})
    _run(DoradoCollector("d", "Dorado", _cfg()), fake2, monkeypatch)
    assert fake2.calls[0][2]["scope"] == 0 and fake2.calls[0][2]["username"] == "svc"


def test_dorado_other_login_error_is_not_an_auth_failure(monkeypatch):
    busy = {"data": {}, "error": {"code": 1077949069, "description": "The number of sessions has reached the upper limit."}}
    fake = Fake({("POST", "/deviceManager/rest/v2/sessions"): (200, busy)})
    payload = _run(DoradoCollector("d", "Dorado", _cfg()), fake, monkeypatch)
    assert payload["status"] == "OFFLINE" and "auth_failed" not in payload


# ── DataDomain ──────────────────────────────────────────────────────────────
def test_datadomain_stops_at_first_refusal(monkeypatch):
    fake = Fake({("POST", f"/rest/{v}/auth"): (401, {}) for v in ("v1.0", "v2.0", "v3.0")})
    payload = _run(DataDomainCollector("d", "DD", _cfg(port=3009)), fake, monkeypatch)
    assert payload["auth_failed"] is True
    assert len([c for c in fake.calls if c[1].endswith("/auth")]) == 1   # et non 3 échecs par collecte


# ── Planificateur et reprise ────────────────────────────────────────────────
DEVICE = {"display_name": "Dorado Makepe", "hostname_ip": "dorado.example.local", "device_type": "HUAWEI_DORADO",
          "port": 8088, "username": "svc", "password": "faux"}


def _rejecting_collector(calls):
    class Rejecting:
        def __init__(self, *a, **kw): ...
        def collect(self):
            calls.append(1)
            p = {"status": "OFFLINE", "metrics": {}, "auth_failed": True,
                 "alerts": [{"severity": "CRITICAL", "message": "Identifiants refusés par la baie", "component": "x"}]}
            return p
    return Rejecting


def test_rejected_credentials_suspend_collection_until_admin_acts(client, admin_headers, monkeypatch, db):
    dev = client.post("/api/inventory/devices", headers=admin_headers, json=DEVICE).json()
    calls, queued = [], []
    monkeypatch.setitem(scheduler.COLLECTOR_REGISTRY, "HUAWEI_DORADO", _rejecting_collector(calls))
    monkeypatch.setattr(scheduler.collect_device, "delay", lambda device_id: queued.append(device_id))

    scheduler.collect_device(dev["id"])
    db.expire_all()
    row = db.get(InfrastructureDevice, dev["id"])
    assert row.last_sync_status == AUTH_FAILED and "collecte suspendue" in row.last_sync_error

    # Plus aucun essai : ni par le répartiteur, ni par une tâche déjà en file.
    r_client.flushall()
    assert scheduler.dispatch_polling() == 0 and queued == []
    scheduler.collect_device(dev["id"])
    assert calls == [1]

    # Réactiver l'équipement ne suffit pas (même mot de passe = même échec)…
    client.put(f"/api/inventory/devices/{dev['id']}", headers=admin_headers, json={"is_active": True})
    db.expire_all()
    assert db.get(InfrastructureDevice, dev["id"]).last_sync_status == AUTH_FAILED
    # … ressaisir le mot de passe relance la collecte.
    res = client.put(f"/api/inventory/devices/{dev['id']}", headers=admin_headers, json={"password": "corrige"})
    assert res.json()["last_sync_status"] == "UNSYNCED"
    assert scheduler.dispatch_polling() == 1


def test_resume_endpoint(client, admin_headers, monkeypatch, db):
    dev = client.post("/api/inventory/devices", headers=admin_headers, json=DEVICE).json()
    assert client.post(f"/api/inventory/devices/{dev['id']}/resume", headers=admin_headers).status_code == 409
    monkeypatch.setitem(scheduler.COLLECTOR_REGISTRY, "HUAWEI_DORADO", _rejecting_collector([]))
    scheduler.collect_device(dev["id"])
    r_client.set(NEXT_POLL_KEY.format(device_id=dev["id"]), "1")
    res = client.post(f"/api/inventory/devices/{dev['id']}/resume", headers=admin_headers)
    assert res.status_code == 200 and res.json()["last_sync_status"] == "UNSYNCED"
    assert r_client.get(NEXT_POLL_KEY.format(device_id=dev["id"])) is None   # collecte au prochain passage
    audit = client.get("/api/auth/audit", headers=admin_headers).json()
    actions = [a["action"] for a in (audit["items"] if isinstance(audit, dict) else audit)]
    assert "device.auth_suspended" in actions and "device.collection_resumed" in actions


def test_collector_exception_classified_too(client, admin_headers, monkeypatch, db):
    dev = client.post("/api/inventory/devices", headers=admin_headers, json=DEVICE).json()

    class Raises:
        def __init__(self, *a, **kw): ...
        def collect(self):
            raise CredentialsRejected("refusé")
    monkeypatch.setitem(scheduler.COLLECTOR_REGISTRY, "HUAWEI_DORADO", Raises)
    scheduler.collect_device(dev["id"])
    db.expire_all()
    assert db.get(InfrastructureDevice, dev["id"]).last_sync_status == AUTH_FAILED
