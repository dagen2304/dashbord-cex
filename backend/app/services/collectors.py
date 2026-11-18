"""Registre unique des collecteurs : type d'équipement -> classe."""
from app.services.datadomain_collector import DataDomainCollector
from app.services.dorado_collector import DoradoCollector
from app.services.netbackup_collector import NetBackupCollector
from app.services.powerstore_collector import PowerStoreCollector
from app.services.scality_collector import ScalityCollector
from app.services.unity_collector import UnityCollector
from app.services.vcenter_collector import VCenterCollector
from app.services.veritas_collector import VeritasCollector
from app.services.vrops_collector import MIN_INTERVAL_S as VROPS_MIN_INTERVAL_S, VRopsCollector

COLLECTOR_REGISTRY = {
    "VCENTER": VCenterCollector,
    "HUAWEI_DORADO": DoradoCollector,
    "DELL_UNITY": UnityCollector,
    "DELL_POWERSTORE": PowerStoreCollector,
    "DATADOMAIN": DataDomainCollector,
    "NETBACKUP": NetBackupCollector,
    "SCALITY": ScalityCollector,
    "VERITAS_ACCESS": VeritasCollector,
    "VROPS": VRopsCollector,
}

# Intervalle minimal entre deux collectes, par type (la source ne se rafraîchit pas plus vite).
MIN_INTERVAL_S = {"VROPS": VROPS_MIN_INTERVAL_S}


def effective_interval(device_type: str, interval_s: int) -> int:
    """Intervalle réellement appliqué à un type : l'intervalle global, borné par le minimum du type."""
    return max(int(interval_s), MIN_INTERVAL_S.get(device_type, 0))
