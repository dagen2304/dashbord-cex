"""
Collecteur VMware vRealize Operations 8.x (Aria Operations) — Suite API REST, lecture seule.

vROps n'est pas un équipement mais une source d'ANALYSE : il agrège déjà les vCenter qu'il
supervise et calcule ce que la collecte vCenter directe ne sait pas faire :
    - capacité restante et « jours avant saturation » (OnlineCapacityAnalytics) ;
    - scores santé / risque / efficacité ;
    - VMs surdimensionnées, sous-dimensionnées ou inactives (rightsizing) ;
    - alertes et symptômes vROps.
La collecte vCenter directe reste la source du temps réel (latences 20 s, état des hôtes).

Session (documentation « vRealize Operations Manager API », 8.x) :
    - POST /suite-api/api/auth/token/acquire {username, password[, authSource]} → jeton ;
    - en-tête « Authorization: vRealizeOpsToken <jeton> » sur chaque requête ;
    - POST /suite-api/api/auth/token/release en fin de cycle, même si la collecte échoue
      (sinon les jetons restent valides jusqu'à expiration, 6 h par défaut).
Compte d'un annuaire (AD/LDAP déclaré dans vROps) : saisir « SOURCE\\utilisateur » ;
« SOURCE » est le nom de la source d'authentification dans vROps.

Les données de vROps sont recalculées toutes les 5 minutes : le répartiteur applique
un intervalle minimal (MIN_INTERVAL_S) quel que soit l'intervalle global.
"""
import logging
from typing import Any, Dict, Iterable, List, Optional

from app.services.base_collector import BaseCollector

logger = logging.getLogger(__name__)

MIN_INTERVAL_S = 300

# Clés de statistiques vROps 8.x (adapterKind VMWARE). Une clé absente de la réponse
# laisse simplement le champ vide (aucune valeur inventée) et est signalée en INFO.
CLUSTER_KEYS = {
    "cpu|usage_average": "cpu_usage_pct",
    "mem|usage_average": "ram_usage_pct",
    "OnlineCapacityAnalytics|timeRemaining": "time_remaining_days",
    "OnlineCapacityAnalytics|capacityRemainingPercentage": "capacity_remaining_pct",
    "badge|health": "health",
    "badge|risk": "risk",
    "badge|efficiency": "efficiency",
    "summary|number_running_vms": "running_vms",
}
DATASTORE_KEYS = {
    "capacity|total_capacity": "capacity_gb",
    "capacity|used_space": "used_gb",
    "OnlineCapacityAnalytics|timeRemaining": "time_remaining_days",
    "badge|health": "health",
}
VM_KEYS = {
    "summary|oversized": "oversized",
    "summary|undersized": "undersized",
    "summary|idle": "idle",
    "summary|oversized|vcpus": "oversized_vcpus",
    "summary|oversized|memory": "oversized_memory_kb",
    "summary|undersized|vcpus": "undersized_vcpus",
    "summary|undersized|memory": "undersized_memory_kb",
}

# Niveaux d'alerte vROps → niveaux du dashboard (INFORMATION ignoré).
ALERT_LEVELS = {"CRITICAL": "CRITICAL", "IMMEDIATE": "WARNING", "WARNING": "WARNING"}
MAX_ALERTS = {"CRITICAL": 100, "WARNING": 100}
MAX_RIGHTSIZING_ROWS = 300
CAPACITY_WARN_DAYS = 30


class VRopsCollector(BaseCollector):
    _DEFAULT_PORT = 443
    _TIMEOUT = 60
    _PAGE_SIZE = 1000
    _MAX_PAGES = 50          # 50 000 objets par type : garde-fou contre une boucle sans fin
    _STATS_CHUNK = 500       # identifiants par requête stats/latest/query

    def collect(self) -> Dict[str, Any]:
        host = self.config.get("host")
        port = int(self.config.get("port") or self._DEFAULT_PORT)
        self.base = f"https://{host}:{port}/suite-api/api"
        logger.info("[vROps] Démarrage collecte pour '%s' (%s)", self.device_name, host)

        payload = self.get_base_payload(status="HEALTHY")
        payload["device_type"] = "operations"
        session = self.http_session()
        session.headers.update({"Accept": "application/json", "Content-Type": "application/json"})
        token: Optional[str] = None
        try:
            token = self._acquire_token(session)
            session.headers["Authorization"] = f"vRealizeOpsToken {token}"
            self._fill(session, payload)
            return payload
        except Exception as exc:
            logger.error("[vROps] ✗ Erreur de collecte pour %s : %s", host, exc)
            payload.update(status="OFFLINE", metrics={}, alerts=[{
                "severity": "CRITICAL",
                "message": f"Suite API vROps {host}:{port} inaccessible — {type(exc).__name__}: {exc}",
                "component": "vROps-SuiteAPI",
            }])
            return payload
        finally:
            if token:
                self._release_token(session, host)
            session.close()

    # ── Session ─────────────────────────────────────────────────────────────
    def _acquire_token(self, session) -> str:
        user = self.config.get("username") or ""
        body: Dict[str, str] = {"username": user, "password": self.config.get("password") or ""}
        if "\\" in user:
            source, name = user.split("\\", 1)
            body.update(authSource=source, username=name)
        resp = session.post(f"{self.base}/auth/token/acquire", json=body, timeout=self._TIMEOUT)
        if resp.status_code in (401, 403):
            raise PermissionError(f"identifiants refusés par vROps (HTTP {resp.status_code})")
        resp.raise_for_status()
        token = (resp.json() or {}).get("token")
        if not token:
            raise ValueError("réponse d'authentification sans jeton")
        return token

    def _release_token(self, session, host: str) -> None:
        try:
            resp = session.post(f"{self.base}/auth/token/release", timeout=15)
            if resp.status_code not in (200, 204):
                logger.warning("[vROps] Libération du jeton refusée par %s : HTTP %s", host, resp.status_code)
        except Exception as exc:
            logger.warning("[vROps] Libération du jeton impossible sur %s : %s: %s", host, type(exc).__name__, exc)

    # ── Lecture ─────────────────────────────────────────────────────────────
    def _paged(self, session, path: str, list_key: str, params: Dict[str, Any]) -> List[dict]:
        items: List[dict] = []
        for page in range(self._MAX_PAGES):
            resp = session.get(f"{self.base}/{path}", params={**params, "page": page, "pageSize": self._PAGE_SIZE},
                               timeout=self._TIMEOUT)
            resp.raise_for_status()
            data = resp.json() or {}
            batch = data.get(list_key) or []
            items.extend(batch)
            # totalCount fait foi : le serveur peut plafonner pageSize sous la valeur demandée.
            total = (data.get("pageInfo") or {}).get("totalCount")
            if not batch or (len(items) >= int(total) if total is not None else len(batch) < self._PAGE_SIZE):
                break
        return items

    def _resources(self, session, kind: str) -> Dict[str, str]:
        """identifiant vROps → nom, pour un type d'objet vSphere."""
        rows = self._paged(session, "resources", "resourceList", {"adapterKind": "VMWARE", "resourceKind": kind})
        return {r["identifier"]: (r.get("resourceKey") or {}).get("name") or r["identifier"]
                for r in rows if r.get("identifier")}

    def _latest_stats(self, session, ids: Iterable[str], keys: Iterable[str]) -> Dict[str, Dict[str, float]]:
        """Dernière valeur de chaque clé, par objet : {id: {clé: valeur}}."""
        ids, keys = list(ids), list(keys)
        out: Dict[str, Dict[str, float]] = {}
        for i in range(0, len(ids), self._STATS_CHUNK):
            resp = session.post(f"{self.base}/resources/stats/latest/query",
                                json={"resourceId": ids[i:i + self._STATS_CHUNK], "statKey": keys},
                                timeout=self._TIMEOUT)
            resp.raise_for_status()
            for entry in (resp.json() or {}).get("values") or []:
                stats = out.setdefault(entry.get("resourceId"), {})
                for stat in ((entry.get("stat-list") or {}).get("stat")) or []:
                    key = (stat.get("statKey") or {}).get("key")
                    data = stat.get("data") or []
                    if key and data and isinstance(data[-1], (int, float)):
                        stats[key] = float(data[-1])
        return out

    # ── Assemblage ──────────────────────────────────────────────────────────
    def _fill(self, session, payload: Dict[str, Any]) -> None:
        clusters = self._resources(session, "ClusterComputeResource")
        datastores = self._resources(session, "Datastore")
        hosts = self._resources(session, "HostSystem")
        vms = self._resources(session, "VirtualMachine")
        names = {**clusters, **datastores, **hosts, **vms}
        seen_keys: set = set()

        def table(ids: Dict[str, str], keys: Dict[str, str]) -> List[Dict[str, Any]]:
            stats = self._latest_stats(session, ids, keys) if ids else {}
            rows = []
            for rid, name in ids.items():
                values = stats.get(rid, {})
                seen_keys.update(values)
                row: Dict[str, Any] = {"name": name}
                for key, field in keys.items():
                    if key in values:
                        row[field] = round(values[key], 2)
                rows.append(row)
            return rows

        cluster_rows = sorted(table(clusters, CLUSTER_KEYS), key=lambda r: r["name"])
        ds_rows = table(datastores, DATASTORE_KEYS)
        vm_rows = table(vms, VM_KEYS)

        for ds in ds_rows:
            cap, used = ds.pop("capacity_gb", None), ds.pop("used_gb", None)
            if cap:
                ds["capacity_tb"] = round(cap / 1024, 2)
                ds["used_tb"] = round((used or 0) / 1024, 2)
                ds["usage_pct"] = round((used or 0) / cap * 100, 1)
        ds_rows.sort(key=lambda r: r.get("usage_pct", -1), reverse=True)

        rightsizing = []
        for vm in vm_rows:
            for kind in ("oversized", "undersized", "idle"):
                if vm.get(kind, 0) >= 1:
                    row = {"vm_name": vm["name"], "kind": kind}
                    if kind != "idle":
                        if f"{kind}_vcpus" in vm:
                            row["vcpus"] = int(vm[f"{kind}_vcpus"])
                        if f"{kind}_memory_kb" in vm:
                            row["memory_gb"] = round(vm[f"{kind}_memory_kb"] / 1024 / 1024, 1)
                    rightsizing.append(row)
        rank = {"oversized": 0, "undersized": 1, "idle": 2}
        rightsizing.sort(key=lambda r: (rank[r["kind"]], -(r.get("memory_gb") or 0), r["vm_name"]))

        alerts, counts = self._alerts(session, names)

        remaining = [r["time_remaining_days"] for r in cluster_rows + ds_rows if r.get("time_remaining_days") is not None]
        total_tb = sum(r.get("capacity_tb", 0) for r in ds_rows)
        used_tb = sum(r.get("used_tb", 0) for r in ds_rows)
        metrics: Dict[str, Any] = {
            "clusters_total": len(clusters),
            "hosts_total": len(hosts),
            "vms_total": len(vms),
            "alerts_critical": counts["CRITICAL"],
            "alerts_immediate": counts["IMMEDIATE"],
            "alerts_warning": counts["WARNING"],
            "vms_oversized": sum(1 for r in rightsizing if r["kind"] == "oversized"),
            "vms_undersized": sum(1 for r in rightsizing if r["kind"] == "undersized"),
            "vms_idle": sum(1 for r in rightsizing if r["kind"] == "idle"),
        }
        if remaining:
            metrics["capacity_time_remaining_days"] = round(min(remaining), 1)
        if total_tb > 0:
            metrics["datastores_usage_pct"] = round(used_tb / total_tb * 100, 1)

        # Clés jamais renvoyées : signalées (nom différent selon la version, licence, objet absent).
        expected = set()
        for ids, keys in ((clusters, CLUSTER_KEYS), (datastores, DATASTORE_KEYS), (vms, VM_KEYS)):
            if ids:
                expected |= set(keys)
        missing = sorted(expected - seen_keys)
        if missing:
            alerts.append({"severity": "INFO", "component": "vROps-SuiteAPI",
                           "message": f"Métriques non fournies par vROps : {', '.join(missing)}"})

        status = "HEALTHY"
        days = metrics.get("capacity_time_remaining_days")
        if counts["CRITICAL"] or (days is not None and days < CAPACITY_WARN_DAYS):
            status = "WARNING"
        if days is not None and days < CAPACITY_WARN_DAYS:
            alerts.insert(0, {"severity": "WARNING", "component": "Capacité",
                              "message": f"Saturation prévue par vROps dans {days:g} jour(s)"})

        payload.update(
            status=status, metrics=metrics, alerts=alerts,
            clusters_metrics=cluster_rows, vrops_datastores=ds_rows,
            rightsizing=rightsizing[:MAX_RIGHTSIZING_ROWS],
        )
        logger.info("[vROps] ✓ %d cluster(s), %d datastore(s), %d VM(s), %d alerte(s) critique(s)",
                    len(clusters), len(datastores), len(vms), counts["CRITICAL"])

    def _alerts(self, session, names: Dict[str, str]):
        raw = self._paged(session, "alerts", "alerts", {"activeOnly": "true"})
        counts = {"CRITICAL": 0, "IMMEDIATE": 0, "WARNING": 0}
        kept: Dict[str, List[dict]] = {"CRITICAL": [], "WARNING": []}
        for a in raw:
            level = (a.get("alertLevel") or "").upper()
            if (a.get("status") or "ACTIVE").upper() != "ACTIVE" or level not in ALERT_LEVELS:
                continue
            counts[level] += 1
            sev = ALERT_LEVELS[level]
            if len(kept[sev]) < MAX_ALERTS[sev]:
                kept[sev].append({
                    "severity": sev,
                    "component": names.get(a.get("resourceId"), "vROps"),
                    "message": (a.get("alertDefinitionName") or "Alerte vROps")
                               + (" (immédiate)" if level == "IMMEDIATE" else ""),
                })
        return kept["CRITICAL"] + kept["WARNING"], counts
