"""Latences réelles vCenter (PerformanceManager) — faux PerformanceManager, vraies références pyVmomi."""
from types import SimpleNamespace as NS
from unittest.mock import patch

from pyVmomi import vim

from app.services import vsphere_perf as vp
from app.services.vcenter_collector import VCenterCollector

COUNTERS = {
    vp.DS_READ: 101, vp.DS_WRITE: 102, vp.VD_READ: 201, vp.VD_WRITE: 202, "cpu.usage.average": 2,
}


class FakePerfManager:
    """Imite vim.PerformanceManager : perfCounter + QueryPerf(querySpec=[...])."""

    def __init__(self, data, fail_on=None):
        # data : {moId: {(counterId, instance): [échantillons]}}
        self.perfCounter = [
            NS(key=k, rollupType=n.split(".")[2], groupInfo=NS(key=n.split(".")[0]), nameInfo=NS(key=n.split(".")[1]))
            for n, k in COUNTERS.items()
        ]
        self.data, self.fail_on, self.calls = data, fail_on or set(), []

    def QueryPerf(self, querySpec):
        self.calls.append(len(querySpec))
        out = []
        for spec in querySpec:
            assert spec.intervalId == 20 and spec.maxSample == 3
            if spec.entity._moId in self.fail_on:
                raise vim.fault.InvalidArgument()
            wanted = {m.counterId for m in spec.metricId}
            series = [NS(id=NS(counterId=c, instance=i), value=v)
                      for (c, i), v in self.data.get(spec.entity._moId, {}).items() if c in wanted]
            out.append(NS(entity=spec.entity, value=series))
        return out


def test_counter_resolution_reports_missing():
    pm = FakePerfManager({})
    assert vp.counter_ids(pm, [vp.DS_READ, "nope.nope.average"]) == {vp.DS_READ: 101}


def test_datastore_uuid_from_url():
    assert vp.datastore_uuid("ds:///vmfs/volumes/5f1e2a3b-11aa22bb-c0ff-ee0000000001/") == "5f1e2a3b-11aa22bb-c0ff-ee0000000001"
    assert vp.datastore_uuid("ds:///vmfs/volumes/0c1d2e3f-aabbccdd/") == "0c1d2e3f-aabbccdd"  # NFS
    assert vp.datastore_uuid(None) is None


def test_datastore_latency_takes_worst_host_and_ignores_missing_samples():
    h1, h2 = vim.HostSystem("host-1"), vim.HostSystem("host-2")
    pm = FakePerfManager({
        "host-1": {(101, "uuid-a"): [10, 12, 14], (102, "uuid-a"): [3, 3, 3], (101, "uuid-b"): [-1, -1, -1]},
        "host-2": {(101, "uuid-a"): [30, 30, 33], (101, "uuid-x"): [99, 99, 99]},  # uuid-x : datastore inconnu
    })
    out = vp.datastore_latencies(pm, [h1, h2], {"uuid-a": "DS-A", "uuid-b": "DS-B"})
    assert out == {"DS-A": {"read_latency_ms": 31.0, "write_latency_ms": 3.0}}  # DS-B : aucune mesure valide


def test_vm_latency_takes_slowest_disk_and_skips_aggregate():
    vm = vim.VirtualMachine("vm-1")
    pm = FakePerfManager({"vm-1": {(201, "scsi0:0"): [2, 2, 2], (201, "scsi0:1"): [8, 9, 10], (201, ""): [50, 50, 50],
                                   (202, "scsi0:0"): [1, 1, 1]}})
    assert vp.vm_latencies(pm, [vm]) == {vm: {"read_latency_ms": 9.0, "write_latency_ms": 1.0}}


def test_batching_and_failed_batch_does_not_hide_others():
    vms = [vim.VirtualMachine(f"vm-{i}") for i in range(120)]
    data = {f"vm-{i}": {(201, "scsi0:0"): [5, 5, 5]} for i in range(120)}
    pm = FakePerfManager(data, fail_on={"vm-0"})  # le 1er lot (vm-0..vm-49) échoue
    out = vp.vm_latencies(pm, vms)
    assert pm.calls == [50, 50, 20]
    assert len(out) == 70 and vms[0] not in out and vms[119] in out


# ── Intégration dans le collecteur ──────────────────────────────────────────
def _fake_properties(host, vm_on, vm_off):
    hosts = [{"obj": host, "name": "esx01", "parent": NS(name="CL-01"),
              "runtime.connectionState": vim.HostSystem.ConnectionState.connected, "runtime.inMaintenanceMode": False,
              "summary.hardware.cpuMhz": 2000, "summary.hardware.numCpuCores": 10, "summary.hardware.memorySize": 64 * 1024**3,
              "summary.quickStats.overallCpuUsage": 5000, "summary.quickStats.overallMemoryUsage": 32768}]
    vms = [{"obj": vm_on, "name": "vm-app", "runtime.powerState": vim.VirtualMachinePowerState.poweredOn, "runtime.host": host,
            "summary.config.numCpu": 2, "summary.config.memorySizeMB": 4096, "summary.quickStats.overallCpuUsage": 1000,
            "summary.quickStats.guestMemoryUsage": 2048, "config.hardware.device": [], "guest.guestFullName": "RHEL 9",
            "guest.ipAddress": "10.0.0.5", "guest.disk": []},
           {"obj": vm_off, "name": "vm-off", "runtime.powerState": vim.VirtualMachinePowerState.poweredOff}]
    dss = [{"obj": None, "name": "DS-A", "summary.accessible": True, "summary.capacity": 10 * 1024**4,
            "summary.freeSpace": 4 * 1024**4, "summary.url": "ds:///vmfs/volumes/uuid-a/"}]
    return {vim.HostSystem: hosts, vim.VirtualMachine: vms, vim.Datastore: dss}


def _run_collector(perf_manager):
    host, vm_on, vm_off = vim.HostSystem("host-1"), vim.VirtualMachine("vm-1"), vim.VirtualMachine("vm-2")
    props = _fake_properties(host, vm_on, vm_off)
    si = NS(content=NS(perfManager=perf_manager))
    collector = VCenterCollector("dev-1", "vCenter test", {"host": "vc.example", "port": 443, "username": "u", "password": "p"})
    with patch("app.services.vcenter_collector.SmartConnect", return_value=si), \
         patch("app.services.vcenter_collector.Disconnect"), \
         patch.object(VCenterCollector, "_collect_properties", lambda self, _si, vt, _p: props[vt[0]]):
        return collector.collect()


def test_collector_attaches_measured_latencies():
    pm = FakePerfManager({"host-1": {(101, "uuid-a"): [24, 26, 25], (102, "uuid-a"): [4, 4, 4]},
                          "vm-1": {(201, "scsi0:0"): [7, 7, 7], (202, "scsi0:0"): [3, 3, 3]}})
    payload = _run_collector(pm)
    assert payload["status"] == "HEALTHY"
    ds = payload["datastores_metrics"][0]
    assert ds["read_latency_ms"] == 25.0 and ds["write_latency_ms"] == 4.0 and ds["latency_ms"] == 25.0
    assert payload["metrics"]["latency_ms"] == 25.0
    vm = payload["vms_metrics"][0]
    assert vm["vm_name"] == "vm-app" and vm["read_latency_ms"] == 7.0 and vm["write_latency_ms"] == 3.0
    assert len(payload["vms_metrics"]) == 1  # la VM éteinte n'est ni mesurée ni listée


def test_collector_without_perf_data_invents_nothing():
    payload = _run_collector(FakePerfManager({}))
    assert "latency_ms" not in payload["datastores_metrics"][0]
    assert "latency_ms" not in payload["metrics"]
    assert "read_latency_ms" not in payload["vms_metrics"][0]


def test_collector_survives_performance_manager_failure():
    class Broken(FakePerfManager):
        @property
        def perfCounter(self):
            raise vim.fault.NoPermission()

        @perfCounter.setter
        def perfCounter(self, _):
            pass

    payload = _run_collector(Broken({}))
    assert payload["status"] == "HEALTHY"  # le reste de la collecte est conservé
    assert payload["metrics"]["cpu_usage_pct"] == 25.0
    assert any(a["component"] == "PerformanceManager" and a["severity"] == "INFO" for a in payload["alerts"])


# ── Alertes du planificateur ────────────────────────────────────────────────
def test_scheduler_raises_alert_and_emails_once_per_hour(client, admin_headers, db, monkeypatch):
    from app.models.inventory import LatencyAnomaly
    from app.tasks import scheduler

    sent = []
    monkeypatch.setattr(scheduler.send_latency_alert, "delay", lambda *a: sent.append(a))

    def payload():
        return {"status": "HEALTHY", "alerts": [], "datastores_metrics": [
            {"datastore_name": "DS-A", "latency_ms": 34.5}, {"datastore_name": "DS-B", "latency_ms": 3.0},
            {"datastore_name": "DS-C"}]}  # DS-C : pas de mesure → jamais d'alerte

    p1 = payload()
    scheduler._check_latency("dev-1", "vCenter test", p1)
    assert p1["status"] == "WARNING"
    assert [a["message"] for a in p1["alerts"]] == ["Latence DS-A : 34.5 ms (seuil 20 ms)"]
    assert len(sent) == 1 and db.query(LatencyAnomaly).count() == 1

    p2 = payload()
    scheduler._check_latency("dev-1", "vCenter test", p2)
    assert p2["status"] == "WARNING" and len(p2["alerts"]) == 1  # toujours visible…
    assert len(sent) == 1  # …mais pas de second e-mail dans l'heure
