"""Registre unique des collecteurs : type d'équipement -> classe."""
from app.services.datadomain_collector import DataDomainCollector
from app.services.dorado_collector import DoradoCollector
from app.services.netbackup_collector import NetBackupCollector
from app.services.powerstore_collector import PowerStoreCollector
from app.services.scality_collector import ScalityCollector
from app.services.unity_collector import UnityCollector
from app.services.vcenter_collector import VCenterCollector
from app.services.veritas_collector import VeritasCollector

COLLECTOR_REGISTRY = {
    "VCENTER": VCenterCollector,
    "HUAWEI_DORADO": DoradoCollector,
    "DELL_UNITY": UnityCollector,
    "DELL_POWERSTORE": PowerStoreCollector,
    "DATADOMAIN": DataDomainCollector,
    "NETBACKUP": NetBackupCollector,
    "SCALITY": ScalityCollector,
    "VERITAS_ACCESS": VeritasCollector,
}
