"""Session REST Dell Unity : ouverture, cookie, CSRF et déconnexion (faux Unisphere, sans réseau)."""
import json

import requests
from requests.adapters import BaseAdapter

from app.services import base_collector
from app.services.unity_collector import UnityCollector


class FakeUnisphere(BaseAdapter):
    """Imite l'API Unisphere : Basic Auth à la connexion, puis cookie + CSRF."""

    def __init__(self, fail_path=None, login_status=200, logout_status=204):
        super().__init__()
        self.calls, self.fail_path = [], fail_path
        self.login_status, self.logout_status = login_status, logout_status

    def send(self, request, **kwargs):
        path = request.path_url.split("?")[0]
        self.calls.append({"method": request.method, "path": path, "headers": dict(request.headers),
                           "body": request.body})
        resp = requests.Response()
        resp.request, resp.url = request, request.url
        resp.status_code, body, headers = 200, {}, {}
        if path == "/api/types/loginSessionInfo":
            resp.status_code = self.login_status
            headers = {"EMC-CSRF-TOKEN": "csrf-123", "Set-Cookie": "mod_sec_emc=sess-abc; Path=/; Secure"}
        elif path == "/api/types/loginSessionInfo/action/logout":
            resp.status_code = self.logout_status
        elif path == self.fail_path:
            resp.status_code = 500
        elif path == "/api/types/system/instances":
            body = {"entries": [{"content": {"health": {"value": 5}}}]}
        elif path == "/api/types/pool/instances":
            body = {"entries": [{"content": {"sizeTotal": 100 * 1024**4, "sizeUsed": 40 * 1024**4}}]}
        elif path == "/api/types/alert/instances":
            body = {"entries": []}
        resp.headers.update(headers)
        resp._content = json.dumps(body).encode()
        if "Set-Cookie" in headers:
            name, value = headers["Set-Cookie"].split(";")[0].split("=")
            # Cookie posé comme le ferait un vrai serveur.
            self.session.cookies.set(name, value, domain="unity.example", path="/")
        return resp

    def close(self):
        pass


def _run(adapter):
    collector = UnityCollector("dev-1", "Unity test", {"host": "unity.example", "port": 443, "username": "svc",
                                                       "password": "secret", "tls_mode": "insecure"})
    original = base_collector.BaseCollector.http_session

    def fake_session(self):
        s = original(self)
        adapter.session = s
        s.mount("https://", adapter)
        return s

    base_collector.BaseCollector.http_session = fake_session
    try:
        return collector.collect()
    finally:
        base_collector.BaseCollector.http_session = original


def test_session_is_opened_reused_and_closed():
    fake = FakeUnisphere()
    payload = _run(fake)
    assert payload["status"] == "HEALTHY" and payload["metrics"]["capacity_usage_pct"] == 40.0

    login, *middle, logout = fake.calls
    assert login["method"] == "GET" and login["path"] == "/api/types/loginSessionInfo"
    assert login["headers"]["Authorization"].startswith("Basic ")
    for call in middle:  # après la connexion : cookie seul, plus d'identifiants
        assert "Authorization" not in call["headers"]
        assert "mod_sec_emc=sess-abc" in call["headers"].get("Cookie", "")
    assert logout["method"] == "POST" and logout["path"] == "/api/types/loginSessionInfo/action/logout"
    assert logout["headers"]["EMC-CSRF-TOKEN"] == "csrf-123"
    assert json.loads(logout["body"]) == {"localCleanupOnly": True}
    assert not any(c["method"] == "DELETE" for c in fake.calls)


def test_logout_even_when_collection_fails():
    fake = FakeUnisphere(fail_path="/api/types/pool/instances")
    payload = _run(fake)
    assert payload["status"] == "OFFLINE"
    assert fake.calls[-1]["path"] == "/api/types/loginSessionInfo/action/logout"


def test_no_logout_when_login_failed():
    fake = FakeUnisphere(login_status=401)
    payload = _run(fake)
    assert payload["status"] == "OFFLINE"
    assert [c["path"] for c in fake.calls] == ["/api/types/loginSessionInfo"]


def test_refused_logout_does_not_break_collection(caplog):
    fake = FakeUnisphere(logout_status=403)
    payload = _run(fake)
    assert payload["status"] == "HEALTHY"
    assert "Déconnexion refusée" in caplog.text
