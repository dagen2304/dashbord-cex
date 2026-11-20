from app.core.redis_client import r_client
from app.models.application import Application
from app.models.inventory import InfrastructureDevice, MetricHistory, ServiceDiscovery
from app.tasks import scheduler
from app.tasks.celery_app import celery_app


def test_demo_mode_is_off_by_default():
    assert "refresh-demo-data" not in celery_app.conf.beat_schedule
    assert "app.demo.tasks" not in (celery_app.conf.include or [])


def test_demo_refresh_seeds_and_feeds_the_real_pipeline(client, admin_headers, db, monkeypatch):
    from app.demo.tasks import DEVICES, refresh_demo_data

    assert refresh_demo_data() == len(DEVICES)
    devices = db.query(InfrastructureDevice).all()
    assert len(devices) == len(DEVICES)
    assert all(d.hostname_ip.endswith(".demo.invalid") for d in devices)
    assert db.query(MetricHistory).count() > 0
    assert db.query(Application).filter(Application.name == "SAP ERP (démo)").first() is not None

    states = client.get("/api/metrics/realtime", headers=admin_headers).json()
    assert {s["status"] for s in states} <= {"HEALTHY", "WARNING", "CRITICAL"}
    assert all(s["stale"] is False and s["metrics"] for s in states)

    # Un second passage ne recrée pas les équipements.
    refresh_demo_data()
    db.expire_all()
    assert db.query(InfrastructureDevice).count() == len(DEVICES)


def test_dispatcher_never_collects_demo_devices(client, admin_headers, db, monkeypatch):
    from app.demo.tasks import refresh_demo_data

    refresh_demo_data()
    # Même si un administrateur réactive la collecte d'un équipement de démo…
    dev = db.query(InfrastructureDevice).first()
    client.put(f"/api/inventory/devices/{dev.id}", headers=admin_headers, json={"is_active": True})
    queued = []
    monkeypatch.setattr(scheduler.collect_device, "delay", lambda device_id: queued.append(device_id))
    r_client.flushall()
    scheduler.dispatch_polling()
    assert queued == []


def test_demo_vrops_device_and_upgrade_of_existing_demo(client, admin_headers, db):
    from app.demo.tasks import DEVICES, refresh_demo_data

    refresh_demo_data()
    vrops = db.query(InfrastructureDevice).filter(InfrastructureDevice.device_type == "VROPS").one()
    detail = client.get(f"/api/metrics/devices/{vrops.id}", headers=admin_headers).json()
    assert detail["clusters_metrics"] and detail["vrops_datastores"] and detail["rightsizing"]
    assert detail["metrics"]["capacity_time_remaining_days"] > 0

    # Démo créée par une version qui ne connaissait pas vROps : l'équipement est ajouté au passage suivant.
    db.delete(db.query(ServiceDiscovery).filter(ServiceDiscovery.device_id == vrops.id).one())
    db.delete(vrops)
    db.commit()
    refresh_demo_data()
    db.expire_all()
    assert db.query(InfrastructureDevice).count() == len(DEVICES)
