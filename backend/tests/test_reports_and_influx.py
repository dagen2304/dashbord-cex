from datetime import datetime, timedelta
from unittest.mock import MagicMock

from app.api.endpoints.reports import safe_cell
from app.core.influx import InfluxDBManager, build_query, flux_string
from app.models.inventory import InfrastructureDevice, MetricHistory
from app.services.analytics_service import CapacityAnalyticsService


def test_safe_cell_neutralises_formulas():
    assert safe_cell("=HYPERLINK(\"http://x\")") == "'=HYPERLINK(\"http://x\")"
    assert safe_cell("+cmd") == "'+cmd"
    assert safe_cell("@SUM(A1)") == "'@SUM(A1)"
    assert safe_cell("vcenter-01") == "vcenter-01"
    assert safe_cell(-3.5) == -3.5  # les nombres négatifs restent des nombres


def _points(days: int, start_used: float, per_day: float, total: float):
    now = datetime.utcnow()
    return [{"timestamp": now - timedelta(days=days - i), "used_tb": start_used + i * per_day, "total_tb": total}
            for i in range(days)]


def test_projection_on_growing_storage():
    res = CapacityAnalyticsService.calculate_trends(_points(30, 50, 1.0, 200), unit="TB")
    assert res["growth_per_day_tb"] == 1.0
    assert 100 <= res["days_to_full"] <= 125
    assert res["severity"] == "WARNING"


def test_no_projection_for_cpu():
    res = CapacityAnalyticsService.calculate_trends(_points(30, 10, 1.0, 100), unit="%")
    assert res["days_to_full"] is None


def test_zero_total_does_not_crash():
    res = CapacityAnalyticsService.calculate_trends(_points(10, 0, 0, 0), unit="TB")
    assert res["usage_pct"] is None and res["days_to_full"] is None


def test_capacity_report_and_exports(client, admin_headers, db):
    dev = InfrastructureDevice(display_name="=cmd|' /C calc'!A0", hostname_ip="10.0.0.1", device_type="DELL_UNITY",
                               category="STORAGE", port=443, username="u", encrypted_password="x")
    db.add(dev)
    db.flush()
    for p in _points(20, 10, 0.5, 100):
        db.add(MetricHistory(device_id=dev.id, timestamp=p["timestamp"], resource_name="Capacité globale", unit="TB",
                             capacity_total_tb=100, capacity_used_tb=p["used_tb"],
                             capacity_free_tb=100 - p["used_tb"], usage_pct=p["used_tb"]))
    db.commit()

    data = client.get("/api/reports/capacity/data", headers=admin_headers).json()
    assert len(data) == 1 and data[0]["points"] == 20 and data[0]["days_to_full"] is not None

    csv_text = client.get("/api/reports/capacity/csv", headers=admin_headers).content.decode("utf-8-sig")
    assert "'=cmd" in csv_text  # nom piégé neutralisé
    xlsx = client.get("/api/reports/capacity/excel", headers=admin_headers)
    assert xlsx.status_code == 200 and xlsx.content[:2] == b"PK"


def test_flux_queries_never_embed_user_input():
    """Les valeurs utilisateur passent par `params`, jamais dans le texte de la requête."""
    mgr = InfluxDBManager.__new__(InfluxDBManager)
    mgr.query_api = MagicMock()
    mgr.query_api.query.return_value = []
    evil = 'x") |> drop() //'

    mgr.get_clusters(evil)
    mgr.get_vm_filesystems(evil, evil)
    mgr.get_vm_series(evil, "cpu_usage_pct", datetime.utcnow() - timedelta(hours=1), datetime.utcnow())
    mgr.get_vms_latest([evil])
    mgr.get_object_history("Virtual Machine", evil, "cpu_usage_pct", 12)
    mgr.get_vm_saturation(datetime.utcnow() - timedelta(hours=1), datetime.utcnow(), 90, 15, evil, evil, evil)

    assert mgr.query_api.query.call_count == 6
    for call in mgr.query_api.query.call_args_list:
        flux = call.kwargs["query"]
        assert evil not in flux                 # jamais inséré tel quel
        assert 'x\\") |> drop() //' in flux    # présent, mais échappé dans le bloc params


def test_flux_string_escaping():
    assert flux_string('a"b') == '"a\\"b"'
    assert flux_string("${secret}") == '"\\x24{secret}"'
    assert flux_string("a\\") == '"a\\\\"'
    assert flux_string("l1\nl2") == '"l1\\nl2"'
    q = build_query('import "x"\nfrom(bucket: params._b)', {"_b": "bk", "_n": [1, 2]})
    assert q.splitlines()[0] == 'import "x"' and q.splitlines()[1] == 'params = {_b: "bk", _n: [1, 2]}'


def test_unknown_fields_are_refused_without_query():
    mgr = InfluxDBManager.__new__(InfluxDBManager)
    mgr.query_api = MagicMock()
    assert mgr.get_metric_history("dev", "_measurement") == []
    assert mgr.get_object_history("Virtual Machine", "vm", "password", 12) == []
    assert mgr.get_object_history("Table", "vm", "cpu_usage_pct", 12) == []
    mgr.query_api.query.assert_not_called()
