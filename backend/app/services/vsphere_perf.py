"""
Latences disque réelles via le PerformanceManager de vSphere.

Compteurs utilisés (unité : milliseconde, rollup « average ») :
- datastore.totalReadLatency / totalWriteLatency : mesurés par chaque hôte ESXi,
  une instance par datastore (identifiant = UUID tiré de l'URL du datastore) ;
- virtualDisk.totalReadLatency / totalWriteLatency : mesurés par VM, une instance
  par disque virtuel (ex. « scsi0:0 »).

On lit l'intervalle temps réel (échantillons de 20 s) sur la dernière minute
(3 échantillons) et on fait la moyenne de ces échantillons. Pour un datastore vu
par plusieurs hôtes, on retient la latence de l'hôte le plus pénalisé (max) :
c'est la valeur pertinente pour une alerte. Pour une VM à plusieurs disques,
idem (disque le plus lent).

Une valeur absente (VM éteinte, compteur non remonté, -1) n'est jamais remplacée :
le champ n'est simplement pas renseigné.
"""
import logging
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from pyVmomi import vim

logger = logging.getLogger(__name__)

REALTIME_INTERVAL_S = 20
SAMPLES = 3            # dernière minute
BATCH_SIZE = 50        # entités par appel QueryPerf (reste sous config.vpxd.stats.maxQueryMetrics)

DS_READ = "datastore.totalReadLatency.average"
DS_WRITE = "datastore.totalWriteLatency.average"
VD_READ = "virtualDisk.totalReadLatency.average"
VD_WRITE = "virtualDisk.totalWriteLatency.average"


def counter_ids(perf_manager, names: Iterable[str]) -> Dict[str, int]:
    """Résout les noms « groupe.compteur.rollup » en identifiants (propres à chaque vCenter)."""
    wanted = set(names)
    found: Dict[str, int] = {}
    for c in perf_manager.perfCounter or []:
        full = f"{c.groupInfo.key}.{c.nameInfo.key}.{c.rollupType}"
        if full in wanted:
            found[full] = c.key
    missing = wanted - found.keys()
    if missing:
        logger.warning("[vCenter] Compteurs de performance absents : %s", ", ".join(sorted(missing)))
    return found


def _mean(values: Sequence[int]) -> Optional[float]:
    valid = [v for v in values or [] if v is not None and v >= 0]
    return round(sum(valid) / len(valid), 2) if valid else None


def query_latest(perf_manager, entities: List, counter_keys: List[int]) -> Dict[object, Dict[Tuple[int, str], float]]:
    """{entité: {(id compteur, instance): moyenne de la dernière minute}} pour toutes les instances."""
    result: Dict[object, Dict[Tuple[int, str], float]] = {}
    if not entities or not counter_keys:
        return result
    metric_ids = [vim.PerformanceManager.MetricId(counterId=k, instance="*") for k in counter_keys]
    for start in range(0, len(entities), BATCH_SIZE):
        batch = entities[start:start + BATCH_SIZE]
        specs = [vim.PerformanceManager.QuerySpec(entity=e, metricId=metric_ids, intervalId=REALTIME_INTERVAL_S,
                                                   maxSample=SAMPLES, format="normal") for e in batch]
        try:
            series = perf_manager.QueryPerf(querySpec=specs) or []
        except Exception as exc:  # un lot en échec ne doit pas priver les autres de mesures
            logger.warning("[vCenter] QueryPerf en échec pour un lot de %d entités : %s", len(batch), exc)
            continue
        for em in series:
            values = result.setdefault(em.entity, {})
            for s in em.value or []:
                mean = _mean(s.value)
                if mean is not None:
                    values[(s.id.counterId, s.id.instance or "")] = mean
    return result


def datastore_uuid(url: Optional[str]) -> Optional[str]:
    """« ds:///vmfs/volumes/5f1e…-…/ » → « 5f1e…-… » (identifiant d'instance des compteurs datastore.*)."""
    if not url:
        return None
    parts = [p for p in url.rstrip("/").split("/") if p]
    return parts[-1] if parts else None


def datastore_latencies(perf_manager, hosts: List, ds_uuid_to_name: Dict[str, str]) -> Dict[str, Dict[str, float]]:
    """{nom du datastore: {"read_latency_ms", "write_latency_ms"}} — max sur les hôtes qui le voient."""
    ids = counter_ids(perf_manager, [DS_READ, DS_WRITE])
    if not ids:
        return {}
    raw = query_latest(perf_manager, hosts, list(ids.values()))
    key_to_field = {ids.get(DS_READ): "read_latency_ms", ids.get(DS_WRITE): "write_latency_ms"}
    out: Dict[str, Dict[str, float]] = {}
    for values in raw.values():
        for (counter, instance), v in values.items():
            name = ds_uuid_to_name.get(instance)
            field = key_to_field.get(counter)
            if not name or not field:
                continue
            row = out.setdefault(name, {})
            row[field] = max(row.get(field, 0.0), v)
    return out


def vm_latencies(perf_manager, vms: List) -> Dict[object, Dict[str, float]]:
    """{objet VM: {"read_latency_ms", "write_latency_ms"}} — disque le plus lent."""
    ids = counter_ids(perf_manager, [VD_READ, VD_WRITE])
    if not ids:
        return {}
    raw = query_latest(perf_manager, vms, list(ids.values()))
    key_to_field = {ids.get(VD_READ): "read_latency_ms", ids.get(VD_WRITE): "write_latency_ms"}
    out: Dict[object, Dict[str, float]] = {}
    for vm, values in raw.items():
        for (counter, instance), v in values.items():
            field = key_to_field.get(counter)
            if not field or not instance:  # instance vide = agrégat, on garde le détail par disque
                continue
            row = out.setdefault(vm, {})
            row[field] = max(row.get(field, 0.0), v)
    return out
