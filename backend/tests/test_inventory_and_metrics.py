import json
from datetime import datetime, timedelta, timezone

from app.core.redis_client import LATEST_KEY, NEXT_POLL_KEY, r_client
from app.models.inventory import InfrastructureDevice
from app.tasks import scheduler

DEVICE = {
    "display_name": "vCenter PROD", "hostname_ip": "vcenter.example.local", "device_type": "VCENTER",
    "port": 443, "username": "svc_ro", "password": "s3cret",
}


def _create(client, headers, **overrides):
    res = client.post("/api/inventory/devices", headers=headers, json={**DEVICE, **overrides})
    assert res.status_code == 201, res.text
    return res.json()


def test_new_device_is_unsynced_and_category_derived(client, admin_headers):
    dev = _create(client, admin_headers, category="BACKUP")  # catégorie envoyée incohérente : ignorée
    assert dev["last_sync_status"] == "UNSYNCED"
    assert dev["category"] == "VIRTUALIZATION"
    assert "password" not in dev and "encrypted_password" not in dev


def test_device_host_validation(client, admin_headers):
    res = client.post("/api/inventory/devices", headers=admin_headers,
                      json={**DEVICE, "hostname_ip": "http://evil/../x"})
    assert res.status_code == 400


def test_unknown_device_type_rejected(client, admin_headers):
    res = client.post("/api/inventory/devices", headers=admin_headers, json={**DEVICE, "device_type": "TOASTER"})
    assert res.status_code == 400


def test_password_is_encrypted_at_rest(client, admin_headers, db):
    dev = _create(client, admin_headers)
    row = db.get(InfrastructureDevice, dev["id"])
    assert row.encrypted_password != "s3cret"
    assert row.get_password() == "s3cret"


def test_realtime_never_invents_data(client, admin_headers):
    _create(client, admin_headers)
    states = client.get("/api/metrics/realtime", headers=admin_headers).json()
    assert len(states) == 1
    assert states[0]["status"] == "NO_DATA"
    assert states[0]["metrics"] == {}
    assert states[0]["stale"] is True
    assert states[0]["sparkline"]["points"] == []


def test_realtime_reads_worker_state_and_flags_stale(client, admin_headers):
    dev = _create(client, admin_headers)
    fresh = {"status": "HEALTHY", "metrics": {"cpu_usage_pct": 42.0}, "alerts": [],
             "collected_at": datetime.now(timezone.utc).isoformat(), "vms_metrics": [{"vm_name": "x"}] * 3}
    r_client.set(LATEST_KEY.format(device_id=dev["id"]), json.dumps(fresh))
    state = client.get("/api/metrics/realtime", headers=admin_headers).json()[0]
    assert state["status"] == "HEALTHY" and state["metrics"]["cpu_usage_pct"] == 42.0
    assert state["stale"] is False
    assert "vms_metrics" not in state  # données volumineuses exclues de la vue d'ensemble

    old = {**fresh, "collected_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()}
    r_client.set(LATEST_KEY.format(device_id=dev["id"]), json.dumps(old))
    assert client.get("/api/metrics/realtime", headers=admin_headers).json()[0]["stale"] is True


def test_archive_stops_device_from_overview(client, admin_headers):
    dev = _create(client, admin_headers)
    assert client.delete(f"/api/inventory/devices/{dev['id']}", headers=admin_headers).status_code == 200
    assert client.get("/api/metrics/realtime", headers=admin_headers).json() == []
    # Réenregistrer la même adresse réactive l'équipement archivé
    again = _create(client, admin_headers)
    assert again["id"] == dev["id"] and again["is_archived"] is False


def test_settings_validation(client, admin_headers):
    settings = {s["key"]: s for s in client.get("/api/inventory/settings", headers=admin_headers).json()}
    assert "polling_interval" in settings
    put = lambda k, v: client.put(f"/api/inventory/settings/{k}", headers=admin_headers, json={"value": v})
    assert put("polling_interval", "5").status_code == 400      # minimum 30 s
    assert put("polling_interval", "120").status_code == 200
    assert put("alert_email_to", "pas-un-email").status_code == 400
    assert put("alert_email_to", "noc@example.com, astreinte@example.com").json()["value"] == \
        "noc@example.com, astreinte@example.com"
    assert put("smtp_server", "smtp.example.com; rm -rf /").status_code == 400


def test_dispatch_queues_each_device_once_per_interval(client, admin_headers, monkeypatch):
    dev = _create(client, admin_headers)
    queued = []
    monkeypatch.setattr(scheduler.collect_device, "delay", lambda device_id: queued.append(device_id))
    assert scheduler.dispatch_polling() == 1
    assert scheduler.dispatch_polling() == 0  # intervalle pas encore écoulé
    assert queued == [dev["id"]]
    # Modifier l'équipement force une nouvelle collecte au prochain passage
    client.put(f"/api/inventory/devices/{dev['id']}", headers=admin_headers, json={"port": 8443})
    assert r_client.get(NEXT_POLL_KEY.format(device_id=dev["id"])) is None
    assert scheduler.dispatch_polling() == 1


def test_collect_device_stores_state_and_error(client, admin_headers, monkeypatch, db):
    dev = _create(client, admin_headers)

    class Broken:
        def __init__(self, *a, **kw): ...
        def collect(self):
            raise ConnectionError("hôte injoignable")

    monkeypatch.setitem(scheduler.COLLECTOR_REGISTRY, "VCENTER", Broken)
    scheduler.collect_device(dev["id"])
    state = json.loads(r_client.get(LATEST_KEY.format(device_id=dev["id"])))
    assert state["status"] == "ERROR" and state["metrics"] == {}
    db.expire_all()
    row = db.get(InfrastructureDevice, dev["id"])
    assert row.last_sync_status == "OFFLINE"
    assert "hôte injoignable" in row.last_sync_error


def test_collect_device_skips_on_undecryptable_password(client, admin_headers, monkeypatch, db):
    dev = _create(client, admin_headers)
    row = db.get(InfrastructureDevice, dev["id"])
    row.encrypted_password = "garbage"
    db.commit()
    called = []
    monkeypatch.setitem(scheduler.COLLECTOR_REGISTRY, "VCENTER", lambda *a, **kw: called.append(1))
    scheduler.collect_device(dev["id"])
    assert called == []  # aucune tentative de connexion sans mot de passe
    db.expire_all()
    assert db.get(InfrastructureDevice, dev["id"]).last_sync_status == "ERROR"
