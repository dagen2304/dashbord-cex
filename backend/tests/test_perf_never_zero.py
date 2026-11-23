"""Performance des baies : une mesure indisponible reste VIDE (jamais 0) et la cause est signalée.

Régression : Dorado et Unity affichaient IOPS / latence / débit à 0 quand la baie ne les
fournissait pas ; Unity lisait une adresse inexistante (/api/types/system/0) et était donc
toujours à 0.
"""
import json
from urllib.parse import parse_qs, urlsplit

from app.services.dorado_collector import DoradoCollector
from test_auth_suspension import Fake, _cfg, _run
from test_dorado_pools import LOGIN_OK, POOLS
from test_unity_session import FakeUnisphere
from test_unity_session import _run as run_unity

PERF_FIELDS = ("iops", "latency_ms", "throughput_mb")


# ── Dorado ──────────────────────────────────────────────────────────────────
def _dorado(monkeypatch, perf):
    routes = {("POST", "/deviceManager/rest/v2/sessions"): (200, LOGIN_OK),
              ("GET", "/deviceManager/rest/2102/storagepool"): (200, POOLS)}
    if perf is not None:
        routes[("GET", "/deviceManager/rest/2102/performancestatistictask/0")] = perf
    return _run(DoradoCollector("d", "Dorado", _cfg()), Fake(routes), monkeypatch)


def test_dorado_performance_read(monkeypatch):
    m = _dorado(monkeypatch, (200, {"data": {"IOPS": "42000", "BANDWIDTH": "1536000", "LATENCY": "420"}, "error": {"code": 0}}))["metrics"]
    assert (m["iops"], m["throughput_mb"], m["latency_ms"]) == (42000, 1500.0, 0.42)


def test_dorado_performance_unavailable_stays_empty(monkeypatch):
    payload = _dorado(monkeypatch, None)          # 404 sur la tâche de statistiques
    assert not any(f in payload["metrics"] for f in PERF_FIELDS)
    assert payload["metrics"]["capacity_total_tb"] == 300.0   # le reste de la collecte est conservé
    info = [a["message"] for a in payload["alerts"] if a["severity"] == "INFO"]
    assert info and "performance indisponible (HTTP 404" in info[0]


def test_dorado_partial_performance(monkeypatch):
    m = _dorado(monkeypatch, (200, {"data": {"IOPS": "1000"}, "error": {"code": 0}}))["metrics"]
    assert m["iops"] == 1000 and "latency_ms" not in m and "throughput_mb" not in m


# ── Unity ───────────────────────────────────────────────────────────────────
class FakeUnityMetrics(FakeUnisphere):
    """Ajoute l'historique de métriques : valeurs par SP, deux horodatages."""

    def __init__(self, metrics):
        super().__init__()
        self.metrics = metrics

    def send(self, request, **kwargs):
        url = urlsplit(request.url)
        if url.path == "/api/types/metricValue/instances":
            resp = super().send(request, **kwargs)   # journalise l'appel
            path = parse_qs(url.query)["filter"][0].split('"')[1]
            values = self.metrics.get(path)
            entries = [] if values is None else [
                {"content": {"path": path, "timestamp": "2026-11-24T10:00:00.000Z", "values": {"spa": 0, "spb": 0}}},
                {"content": {"path": path, "timestamp": "2026-11-24T10:01:00.000Z", "values": values}}]
            resp._content = json.dumps({"entries": entries}).encode()
            return resp
        return super().send(request, **kwargs)


def test_unity_iops_and_throughput_from_metric_history():
    fake = FakeUnityMetrics({
        "sp.*.storage.summary.readsRate": {"spa": 3000.4, "spb": 2000.2},
        "sp.*.storage.summary.writesRate": {"spa": 1500.0, "spb": 500.0},
        "sp.*.storage.summary.readBytesRate": {"spa": 200 * 1024 ** 2, "spb": 100 * 1024 ** 2},
        "sp.*.storage.summary.writeBytesRate": {"spa": 50 * 1024 ** 2, "spb": 60 * 1024 ** 2},
    })
    payload = run_unity(fake)
    m = payload["metrics"]
    assert m["iops"] == 7001 and m["throughput_mb"] == 410.0    # dernier horodatage, sommé sur les SP
    assert "latency_ms" not in m                                 # non collectée : vide, pas 0
    assert not any(a["severity"] == "INFO" for a in payload["alerts"])
    assert not any("/types/system/0" in c["path"] for c in fake.calls)   # ancienne adresse inexistante


def test_unity_metrics_disabled_stay_empty():
    payload = run_unity(FakeUnityMetrics({}))
    assert not any(f in payload["metrics"] for f in PERF_FIELDS)
    assert payload["metrics"]["capacity_usage_pct"] == 40.0
    info = [a["message"] for a in payload["alerts"] if a["severity"] == "INFO"]
    assert info and "collecte des métriques désactivée" in info[0]
