"""Collecteur vRealize Operations 8.x : faux serveur Suite API (aucun réseau)."""
import json
from urllib.parse import parse_qs, urlsplit

import requests
from requests.adapters import BaseAdapter

from app.services import base_collector
from app.services.collectors import effective_interval
from app.services.vrops_collector import VRopsCollector

RESOURCES = {
    "ClusterComputeResource": [("c1", "CL-PROD"), ("c2", "CL-DEV")],
    "Datastore": [("d1", "DS-01"), ("d2", "DS-02")],
    "HostSystem": [("h1", "esx01"), ("h2", "esx02"), ("h3", "esx03")],
    "VirtualMachine": [(f"vm{i}", f"VM-{i:03d}") for i in range(5)],
}
STATS = {
    "c1": {"cpu|usage_average": 61.25, "mem|usage_average": 78.5, "OnlineCapacityAnalytics|timeRemaining": 120,
           "OnlineCapacityAnalytics|capacityRemainingPercentage": 22, "badge|health": 100, "badge|risk": 25,
           "badge|efficiency": 50, "summary|number_running_vms": 3},
    "c2": {"cpu|usage_average": 12.0, "mem|usage_average": 30.0, "OnlineCapacityAnalytics|timeRemaining": 365},
    "d1": {"capacity|total_capacity": 10240, "capacity|used_space": 8192,
           "OnlineCapacityAnalytics|timeRemaining": 21},
    "d2": {"capacity|total_capacity": 2048, "capacity|used_space": 512},
    "vm0": {"summary|oversized": 1, "summary|oversized|vcpus": 4, "summary|oversized|memory": 16 * 1024 * 1024},
    "vm1": {"summary|undersized": 1, "summary|undersized|vcpus": 2},
    "vm2": {"summary|idle": 1},
    "vm3": {"summary|oversized": 0, "summary|undersized": 0, "summary|idle": 0,
            "cpu|readyPct": 12.0, "cpu|costopPct": 1.0, "virtualDisk|totalLatency": 45.0, "diskspace|snapshot": 120.5},
    "vm4": {"cpu|readyPct": 2.0, "mem|balloonPct": 0.0, "diskspace|snapshot": 8.0},
    "h1": {"cpu|usage_average": 70.0, "mem|usage_average": 81.0, "badge|health": 25},
}
HEALTH = {"h1": "RED", "vm3": "YELLOW"}
PROPS = {
    "h1": {"summary|parentCluster": "CL-PROD", "runtime|connectionState": "connected", "runtime|maintenanceState": "inMaintenance"},
    "vm0": {"summary|parentCluster": "CL-PROD"},
    "vm3": {"summary|parentCluster": "CL-DEV"},
    "c1": {"summary|parentVcenter": "vcsa-prod"},
}
ALERTS = [
    {"alertId": "a1", "resourceId": "h1", "alertLevel": "CRITICAL", "status": "ACTIVE",
     "alertDefinitionName": "Host has lost connection to vCenter", "alertImpact": "HEALTH", "startTimeUTC": 1763460000000},
    {"alertId": "a2", "resourceId": "d1", "alertLevel": "IMMEDIATE", "status": "ACTIVE",
     "alertDefinitionName": "Datastore is running out of disk space"},
    {"alertId": "a3", "resourceId": "vm1", "alertLevel": "WARNING", "status": "ACTIVE",
     "alertDefinitionName": "VM has CPU contention", "alertImpact": "RISK", "startTimeUTC": 1763463600000},
    {"alertId": "a4", "resourceId": "vm2", "alertLevel": "INFORMATION", "status": "ACTIVE",
     "alertDefinitionName": "Ignorée"},
    {"alertId": "a5", "resourceId": "zz", "alertLevel": "CRITICAL", "status": "CANCELED",
     "alertDefinitionName": "Annulée"},
]


class FakeSuiteApi(BaseAdapter):
    def __init__(self, login_status=200, fail_path=None, page_size=None, props_status=200):
        super().__init__()
        self.props_status = props_status
        self.calls, self.login_status, self.fail_path, self.page_size = [], login_status, fail_path, page_size

    def send(self, request, **kwargs):
        url = urlsplit(request.url)
        path, query = url.path, parse_qs(url.query)
        body = json.loads(request.body) if request.body else None
        self.calls.append({"method": request.method, "path": path, "query": query,
                           "headers": dict(request.headers), "body": body})
        resp = requests.Response()
        resp.request, resp.url, resp.status_code = request, request.url, 200
        data = {}
        if path == "/suite-api/api/auth/token/acquire":
            resp.status_code = self.login_status
            data = {"token": "tok-123", "validity": 0} if self.login_status == 200 else {"message": "denied"}
        elif path == "/suite-api/api/auth/token/release":
            data = {}
        elif path == self.fail_path:
            resp.status_code = 500
        elif path == "/suite-api/api/resources":
            rows = RESOURCES[query["resourceKind"][0]]
            size = self.page_size or int(query["pageSize"][0])
            page = int(query["page"][0])
            chunk = rows[page * size:(page + 1) * size]
            data = {"pageInfo": {"totalCount": len(rows), "page": page, "pageSize": size},
                    "resourceList": [{"identifier": i, "resourceKey": {"name": n, "adapterKindKey": "VMWARE"},
                                      "resourceHealth": HEALTH.get(i, "GREEN")} for i, n in chunk]}
        elif path == "/suite-api/api/resources/stats/latest/query":
            values = []
            for rid in body["resourceId"]:
                stats = [{"statKey": {"key": k}, "timestamps": [1], "data": [v]}
                         for k, v in STATS.get(rid, {}).items() if k in body["statKey"]]
                values.append({"resourceId": rid, "stat-list": {"stat": stats}})
            data = {"values": values}
        elif path == "/suite-api/api/resources/properties":
            if self.props_status != 200:
                resp.status_code = self.props_status
            else:
                assert len(query["resourceId"]) <= 100
                data = {"resourcePropertiesList": [
                    {"resourceId": rid, "property": [{"name": k, "value": v} for k, v in PROPS.get(rid, {}).items()]}
                    for rid in query["resourceId"]]}
        elif path == "/suite-api/api/alerts":
            data = {"pageInfo": {"totalCount": len(ALERTS)}, "alerts": ALERTS}
        else:
            resp.status_code = 404
        resp._content = json.dumps(data).encode()
        return resp

    def close(self):
        pass


def _run(adapter, username="svc-noc"):
    collector = VRopsCollector("dev-v", "vROps test", {"host": "vrops.example", "port": 443, "username": username,
                                                       "password": "secret", "tls_mode": "insecure"})
    original = base_collector.BaseCollector.http_session

    def fake_session(self):
        s = original(self)
        s.mount("https://", adapter)
        return s

    base_collector.BaseCollector.http_session = fake_session
    try:
        return collector.collect()
    finally:
        base_collector.BaseCollector.http_session = original


def test_token_is_acquired_used_and_released():
    fake = FakeSuiteApi()
    payload = _run(fake)
    assert payload["device_type"] == "operations"
    login, *middle, logout = fake.calls
    assert login["path"].endswith("/auth/token/acquire")
    assert login["body"] == {"username": "svc-noc", "password": "secret"}
    for call in middle + [logout]:
        assert call["headers"]["Authorization"] == "vRealizeOpsToken tok-123"
    assert logout["method"] == "POST" and logout["path"].endswith("/auth/token/release")


def test_directory_account_uses_auth_source():
    fake = FakeSuiteApi()
    _run(fake, username="ADCM\\svc-noc")
    assert fake.calls[0]["body"] == {"username": "svc-noc", "password": "secret", "authSource": "ADCM"}


def test_metrics_capacity_rightsizing_and_alerts():
    payload = _run(FakeSuiteApi())
    m = payload["metrics"]
    assert (m["clusters_total"], m["hosts_total"], m["vms_total"]) == (2, 3, 5)
    assert (m["alerts_critical"], m["alerts_immediate"], m["alerts_warning"]) == (1, 1, 1)
    assert (m["vms_oversized"], m["vms_undersized"], m["vms_idle"]) == (1, 1, 1)
    assert (m["reclaimable_vcpus"], m["reclaimable_memory_gb"]) == (4, 16.0)
    assert m["snapshots_gb"] == 128.5 and m["vms_contention"] == 1
    assert m["capacity_time_remaining_days"] == 21          # minimum clusters + datastores
    assert m["datastores_usage_pct"] == round(8704 / 12288 * 100, 1)

    prod = next(c for c in payload["clusters_metrics"] if c["name"] == "CL-PROD")
    assert prod == {"name": "CL-PROD", "color": "GREEN", "vcenter": "vcsa-prod", "cpu_usage_pct": 61.25,
                    "ram_usage_pct": 78.5, "time_remaining_days": 120, "capacity_remaining_pct": 22, "health": 100,
                    "risk": 25, "efficiency": 50, "running_vms": 3}
    dev = next(c for c in payload["clusters_metrics"] if c["name"] == "CL-DEV")
    assert "health" not in dev and "vcenter" not in dev      # valeur absente = champ absent, jamais 0

    assert payload["vrops_datastores"][0] == {"name": "DS-01", "color": "GREEN", "capacity_tb": 10.0, "used_tb": 8.0,
                                              "usage_pct": 80.0, "time_remaining_days": 21}

    rs = payload["rightsizing"]
    assert rs[0] == {"vm_name": "VM-000", "kind": "oversized", "vcpus": 4, "memory_gb": 16.0, "cluster": "CL-PROD"}
    assert {r["kind"] for r in rs} == {"oversized", "undersized", "idle"}
    assert all("id" not in r for r in rs)


def test_hosts_contention_snapshots_and_health():
    payload = _run(FakeSuiteApi())
    h1 = next(h for h in payload["vrops_hosts"] if h["name"] == "esx01")
    assert h1 == {"name": "esx01", "color": "RED", "cpu_usage_pct": 70.0, "ram_usage_pct": 81.0, "health": 25,
                  "cluster": "CL-PROD", "connection": "connected", "maintenance": "inMaintenance"}
    # Seule VM-003 dépasse un seuil (ready 12 % > 5, latence 45 ms > 20) ; VM-004 (ready 2 %) non.
    assert [c["vm_name"] for c in payload["vm_contention"]] == ["VM-003"]
    c = payload["vm_contention"][0]
    assert c["cluster"] == "CL-DEV" and c["cpu_ready_pct"] == 12.0 and c["score"] == round(12 / 5 + 45 / 20, 2)
    assert [s["vm_name"] for s in payload["snapshots"]] == ["VM-003", "VM-004"]
    assert payload["health_summary"]["Hôte"] == {"GREEN": 2, "YELLOW": 0, "ORANGE": 0, "RED": 1, "GREY": 0}
    assert payload["health_summary"]["VM"]["YELLOW"] == 1


def test_alerts_center_and_tab():
    payload = _run(FakeSuiteApi())
    center = payload["alerts"]
    assert center[0]["component"] == "Capacité" and "21 jour" in center[0]["message"]
    assert [a["severity"] for a in center[1:]] == ["CRITICAL", "WARNING"]   # critique + immédiate seulement
    assert center[1] == {"severity": "CRITICAL", "component": "esx01", "message": "Host has lost connection to vCenter"}
    assert not any("contention" in a["message"] for a in center)           # avertissements : onglet seulement

    tab = payload["vrops_alerts"]
    assert [a["level"] for a in tab] == ["CRITICAL", "IMMEDIATE", "WARNING", "INFORMATION"]
    assert tab[0] == {"level": "CRITICAL", "impact": "HEALTH", "name": "Host has lost connection to vCenter",
                      "object": "esx01", "object_kind": "Hôte", "started_at": "2025-11-18T10:00:00+00:00"}
    assert not any(a["name"] == "Annulée" for a in tab)
    assert payload["status"] == "WARNING"


def test_missing_stat_keys_are_reported_not_invented():
    payload = _run(FakeSuiteApi())
    missing = payload["vrops_missing_keys"]
    assert "badge|health" not in missing                     # fourni pour au moins un objet
    assert "summary|undersized|memory" in missing            # jamais fourni
    assert not any(a["severity"] == "INFO" for a in payload["alerts"])   # plus de bruit dans le centre d'alertes


def test_properties_are_optional():
    payload = _run(FakeSuiteApi(props_status=403))
    assert payload["status"] != "OFFLINE"
    assert "cluster" not in payload["vrops_hosts"][0]
    assert any("Propriétés" in d for d in payload["vrops_diagnostics"])


def test_resources_are_paged():
    fake = FakeSuiteApi(page_size=2)
    payload = _run(fake)
    assert payload["metrics"]["vms_total"] == 5
    pages = [c["query"]["page"][0] for c in fake.calls
             if c["path"].endswith("/resources") and c["query"]["resourceKind"] == ["VirtualMachine"]]
    assert pages == ["0", "1", "2"]


def test_token_released_even_when_collection_fails():
    fake = FakeSuiteApi(fail_path="/suite-api/api/alerts")
    payload = _run(fake)
    assert payload["status"] == "OFFLINE" and payload["metrics"] == {}
    assert fake.calls[-1]["path"].endswith("/auth/token/release")


def test_refused_credentials_no_release():
    fake = FakeSuiteApi(login_status=401)
    payload = _run(fake)
    assert payload["status"] == "OFFLINE"
    assert "identifiants refusés" in payload["alerts"][0]["message"]
    assert len(fake.calls) == 1


def test_minimum_interval_for_vrops():
    assert effective_interval("VROPS", 60) == 300
    assert effective_interval("VROPS", 900) == 900
    assert effective_interval("VCENTER", 60) == 60


def test_vrops_is_a_declared_vendor(client, admin_headers):
    vendors = {v["device_type"]: v for v in client.get("/api/inventory/vendors", headers=admin_headers).json()}
    assert vendors["VROPS"]["port"] == 443
