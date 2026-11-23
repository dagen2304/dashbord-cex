"""Dell PowerStore : session, métriques calculées (/metrics/generate) et alertes — faux PowerStore Manager.

Régression : la capacité affichait 100 % (somme des tailles de volumes utilisée comme « total »
ET « utilisé »), IOPS / latence / débit à 0 et aucune alerte. Valeurs de référence : écran
Storage Manager de CMMKP-STG-POWERSTORE01 (175,8 To physiques, 46,0 To libres, 73,9 %).
"""
import json

import requests
from requests.adapters import BaseAdapter

from app.services import base_collector
from app.services.powerstore_collector import PowerStoreCollector

TB = 1024 ** 4
SPACE = [{"timestamp": "2026-11-24T09:50:00Z", "physical_total": 100 * TB, "physical_used": 10 * TB, "data_reduction": 2.0},
         {"timestamp": "2026-11-24T09:55:00Z", "physical_total": 175.8 * TB, "physical_used": 129.8 * TB,
          "data_reduction": 3.2, "logical_provisioned": 854.67 * TB}]
PERF = [{"timestamp": "2026-11-24T09:59:40Z", "total_iops": 8000.0, "avg_latency": 550.0, "total_bandwidth": 613.4 * 1024 ** 2}]
ALERTS = [
    {"severity": "Major", "description_l10n": "Physical capacity utilization is above 70 percent.", "resource_name": "CMMKP-STG-POWERSTORE01-appliance-1", "state": "ACTIVE"},
    {"severity": "Major", "description_l10n": "Remote system unreachable.", "resource_name": "rs-1", "state": "ACTIVE"},
    {"severity": "Minor", "description_l10n": "SSH enabled.", "resource_name": "security_config", "state": "ACTIVE"},
    {"severity": "Minor", "description_l10n": "NTP drift.", "resource_name": "cluster", "state": "ACTIVE"},
]


class FakePowerStore(BaseAdapter):
    def __init__(self, metrics_status=200, login_status=200):
        super().__init__()
        self.calls, self.metrics_status, self.login_status = [], metrics_status, login_status

    def send(self, request, **kwargs):
        url = requests.utils.urlparse(request.url)
        body = json.loads(request.body) if request.body else None
        self.calls.append({"method": request.method, "path": url.path, "query": url.query,
                           "headers": dict(request.headers), "body": body})
        resp = requests.Response()
        resp.request, resp.url, resp.status_code = request, request.url, 200
        data, headers = [], {}
        if url.path == "/api/rest/login_session":
            resp.status_code = self.login_status
            headers = {"DELL-EMC-TOKEN": "csrf-xyz", "Set-Cookie": "auth_cookie=sess; Path=/; Secure"}
            data = [{"id": "s1"}]
        elif url.path == "/api/rest/appliance":
            data = [{"id": "A1", "name": "CMMKP-STG-POWERSTORE01-appliance-1", "model": "PowerStore 1200T"}]
        elif url.path == "/api/rest/metrics/generate":
            resp.status_code = self.metrics_status
            data = {"space_metrics_by_appliance": SPACE, "performance_metrics_by_appliance": PERF}.get(body["entity"], [])
        elif url.path == "/api/rest/alert":
            data = ALERTS
        elif url.path == "/api/rest/logout":
            resp.status_code = 204
        resp.headers.update(headers)
        resp._content = json.dumps(data).encode()
        if "Set-Cookie" in headers:
            self.session.cookies.set("auth_cookie", "sess", domain="ps.example", path="/")
        return resp

    def close(self):
        pass


def _run(fake, monkeypatch):
    original = base_collector.BaseCollector.http_session

    def fake_session(self):
        s = original(self)
        fake.session = s
        s.mount("https://", fake)
        return s

    monkeypatch.setattr(base_collector.BaseCollector, "http_session", fake_session)
    return PowerStoreCollector("p", "PowerStore", {"host": "ps.example", "port": 443, "username": "svc",
                                                   "password": "x", "tls_mode": "insecure"}).collect()


def test_capacity_performance_match_storage_manager(monkeypatch):
    m = _run(FakePowerStore(), monkeypatch)["metrics"]
    assert (m["capacity_total_tb"], m["capacity_used_tb"], m["capacity_usage_pct"]) == (175.8, 129.8, 73.8)
    assert m["iops"] == 8000 and m["latency_ms"] == 0.55 and m["throughput_mb"] == 613.4
    assert m["data_reduction_ratio"] == 3.2   # point le plus récent, pas le premier de la série


def test_alerts_and_status(monkeypatch):
    payload = _run(FakePowerStore(), monkeypatch)
    assert payload["status"] == "WARNING"
    assert [a["severity"] for a in payload["alerts"]] == ["WARNING"] * 4
    assert payload["alerts"][0]["message"] == "Physical capacity utilization is above 70 percent. (majeure)"


def test_session_csrf_and_logout(monkeypatch):
    fake = FakePowerStore()
    _run(fake, monkeypatch)
    login, *middle, logout = fake.calls
    assert login["path"] == "/api/rest/login_session" and login["headers"]["Authorization"].startswith("Basic ")
    for c in middle:
        assert "Authorization" not in c["headers"]                 # cookie seul après connexion
        if c["method"] == "POST":
            assert c["headers"]["DELL-EMC-TOKEN"] == "csrf-xyz"   # jeton anti-CSRF exigé sur les POST
    assert logout["method"] == "POST" and logout["path"] == "/api/rest/logout"


def test_unreadable_metrics_stay_empty_never_invented(monkeypatch):
    payload = _run(FakePowerStore(metrics_status=403), monkeypatch)
    m = payload["metrics"]
    for f in ("capacity_usage_pct", "capacity_total_tb", "iops", "latency_ms", "throughput_mb", "data_reduction_ratio"):
        assert f not in m, f      # ni 0, ni 100 %, ni somme des volumes
    info = [a for a in payload["alerts"] if a["severity"] == "INFO"]
    assert info and "space_metrics_by_appliance indisponible (HTTP 403)" in info[0]["message"]


def test_refused_credentials_are_flagged(monkeypatch):
    payload = _run(FakePowerStore(login_status=401), monkeypatch)
    assert payload["status"] == "OFFLINE" and payload["auth_failed"] is True
