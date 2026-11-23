"""Scality RING Supervisor : requêtes acceptées par l'API (Flask / Eve) et aucune valeur inventée.

Régression : HTTP 400 « The browser (or proxy) sent a request that this server could not
understand » — les GET partaient avec Content-Type: application/json et un corps vide.
"""
import json

import requests
from requests.adapters import BaseAdapter

from app.services import base_collector
from app.services.scality_collector import ScalityCollector

TB = 1024 ** 4
RING = {"_items": [{"id": "META", "name": "META", "status": "OK"},
                   {"id": "DATA", "name": "DATA", "status": "OK", "diskspace_total": 400 * TB,
                    "diskspace_used": 236 * TB, "number_of_nodes": 6}]}


class FakeSupervisor(BaseAdapter):
    """Imite Flask : un Content-Type JSON avec un corps vide est refusé en HTTP 400."""

    def __init__(self, rings=RING):
        super().__init__()
        self.rings, self.calls = rings, []

    def send(self, request, **kwargs):
        self.calls.append(dict(request.headers))
        resp = requests.Response()
        resp.request, resp.url, resp.status_code = request, request.url, 200
        if request.headers.get("Content-Type", "").startswith("application/json") and not request.body:
            resp.status_code = 400
            data = {"message": "The browser (or proxy) sent a request that this server could not understand."}
        elif request.path_url.startswith("/api/v0.1/rings/"):
            data = self.rings
        else:
            data = {"supapi_version": "8.5"}
        resp._content = json.dumps(data).encode()
        return resp

    def close(self):
        pass


def _run(fake, monkeypatch):
    original = base_collector.BaseCollector.http_session

    def fake_session(self):
        s = original(self)
        s.mount("https://", fake)
        return s

    monkeypatch.setattr(base_collector.BaseCollector, "http_session", fake_session)
    return ScalityCollector("s", "RING", {"host": "ring.example", "port": 443, "username": "svc", "password": "x",
                                          "tls_mode": "insecure"}).collect()


def test_get_requests_have_no_json_content_type(monkeypatch):
    fake = FakeSupervisor()
    payload = _run(fake, monkeypatch)
    assert payload["status"] == "HEALTHY", payload["alerts"]
    assert all("Content-Type" not in h for h in fake.calls)
    m = payload["metrics"]
    assert (m["capacity_total_tb"], m["capacity_used_tb"], m["capacity_usage_pct"], m["total_nodes"]) == (400.0, 236.0, 59.0, 6)


def test_no_invented_values(monkeypatch):
    m = _run(FakeSupervisor(), monkeypatch)["metrics"]
    assert "iops_s3" not in m          # jamais fourni par cette API : vide, pas 0
    assert "active_nodes" not in m     # pas une copie du nombre total de nœuds


def test_missing_capacity_and_degraded_ring(monkeypatch):
    rings = {"_items": [{"id": "DATA", "name": "DATA", "status": "DEGRADED", "number_of_nodes": 6}]}
    payload = _run(FakeSupervisor(rings), monkeypatch)
    assert "capacity_usage_pct" not in payload["metrics"]
    msgs = [(a["severity"], a["message"]) for a in payload["alerts"]]
    assert ("WARNING", "État du ring : DEGRADED") in msgs
    assert any(s == "INFO" and "capacité" in m for s, m in msgs)
