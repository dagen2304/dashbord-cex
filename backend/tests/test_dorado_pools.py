"""Huawei Dorado : statistiques par pool de stockage (faux DeviceManager, sans réseau)."""
from app.models.inventory import MetricHistory
from app.services.dorado_collector import DoradoCollector
from app.tasks import scheduler
from test_auth_suspension import Fake, _cfg, _run

TB_SECTORS = 1024 ** 4 // 512   # 1 To exprimé en secteurs de 512 octets
LOGIN_OK = {"data": {"iBaseToken": "tok", "deviceid": "2102"}, "error": {"code": 0}}
POOLS = {"data": [
    {"ID": "0", "NAME": "StoragePool_SAN", "HEALTHSTATUS": "1", "RUNNINGSTATUS": "27",
     "USERTOTALCAPACITY": str(100 * TB_SECTORS), "USERCONSUMEDCAPACITY": str(95 * TB_SECTORS),
     "USERFREECAPACITY": str(5 * TB_SECTORS), "SUBSCRIBEDCAPACITY": str(140 * TB_SECTORS),
     "USERCONSUMEDCAPACITYTHRESHOLD": "85"},
    {"ID": "1", "NAME": "StoragePool_NAS", "HEALTHSTATUS": "5", "RUNNINGSTATUS": "27",
     "USERTOTALCAPACITY": str(200 * TB_SECTORS), "USERCONSUMEDCAPACITY": str(50 * TB_SECTORS)},
], "error": {"code": 0}}


def _collect(monkeypatch):
    fake = Fake({("POST", "/deviceManager/rest/v2/sessions"): (200, LOGIN_OK),
                 ("GET", "/deviceManager/rest/2102/storagepool"): (200, POOLS)})
    return _run(DoradoCollector("d", "Dorado", _cfg()), fake, monkeypatch)


def test_each_pool_is_reported(monkeypatch):
    payload = _collect(monkeypatch)
    san, nas = payload["storage_pools"]           # triés du plus rempli au moins rempli
    assert san == {"name": "StoragePool_SAN", "id": "0", "capacity_tb": 100.0, "used_tb": 95.0, "usage_pct": 95.0,
                   "free_tb": 5.0, "subscribed_tb": 140.0, "subscription_pct": 140.0, "alarm_threshold_pct": 85.0,
                   "health": "NORMAL", "running": "ONLINE"}
    assert nas["usage_pct"] == 25.0 and nas["free_tb"] == 150.0 and nas["health"] == "DEGRADED"
    assert "subscription_pct" not in nas and "alarm_threshold_pct" not in nas   # absent = non fourni
    # Total de la baie inchangé.
    assert payload["metrics"]["capacity_usage_pct"] == round(145 / 300 * 100, 1)


def test_full_pool_is_not_hidden_by_array_average(monkeypatch):
    payload = _collect(monkeypatch)
    assert payload["metrics"]["capacity_usage_pct"] < 50          # la moyenne masquerait le problème…
    msgs = [(a["severity"], a["message"]) for a in payload["alerts"]]
    assert ("CRITICAL", "Pool StoragePool_SAN à 95 % (95 / 100 To, seuil 85 %)") in msgs   # … pas l'alerte par pool
    assert ("WARNING", "Pool StoragePool_NAS : état dégradé") in msgs
    assert payload["status"] == "CRITICAL"


def test_capacity_history_per_pool(client, db):   # client : crée les tables
    scheduler._store_capacity_history("dev-p", {
        "device_type": "storage", "metrics": {"capacity_total_tb": 300, "capacity_used_tb": 145},
        "storage_pools": [{"name": "StoragePool_SAN", "capacity_tb": 100.0, "used_tb": 95.0}]})
    rows = {r.resource_name: r.usage_pct for r in db.query(MetricHistory).filter(MetricHistory.device_id == "dev-p")}
    assert rows == {"Capacité globale": 48.33, "Pool StoragePool_SAN": 95.0}
