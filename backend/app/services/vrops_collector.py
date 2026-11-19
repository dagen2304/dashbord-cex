"""
Collecteur VMware vRealize Operations 8.x (Aria Operations) — Suite API REST, lecture seule.

vROps n'est pas un équipement mais une source d'ANALYSE : il agrège déjà les vCenter qu'il
supervise et calcule ce que la collecte vCenter directe ne sait pas faire. Le collecteur
alimente l'onglet « vRealize » du dashboard :
    - Synthèse   : santé des objets (vert / jaune / orange / rouge), scores des clusters ;
    - Capacité   : capacité restante et jours avant saturation (CPU, mémoire, disque) ;
    - Performance: hôtes, VMs en contention (CPU ready, co-stop, ballooning, latence disque) ;
    - Optimisation : VMs surdimensionnées / sous-dimensionnées / inactives, snapshots,
                     capacité récupérable ;
    - Alertes    : alertes vROps actives avec leur impact (santé, risque, efficacité).
La collecte vCenter directe reste la source du temps réel (latences 20 s, état des hôtes).

Session (documentation « vRealize Operations Manager API », 8.x) :
    - POST /suite-api/api/auth/token/acquire {username, password[, authSource]} → jeton ;
    - en-tête « Authorization: vRealizeOpsToken <jeton> » sur chaque requête ;
    - POST /suite-api/api/auth/token/release en fin de cycle, même si la collecte échoue.
Compte d'un annuaire (AD/LDAP déclaré dans vROps) : saisir « SOURCE\\utilisateur » ;
« SOURCE » est le nom de la source d'authentification dans vROps (ex. LDAP\\ZZZZHDNS).

Aucune valeur inventée : une métrique que vROps ne renvoie pas reste absente, et la liste
des clés jamais renvoyées est publiée (`vrops_missing_keys`) pour ajuster les noms.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from app.services.base_collector import BaseCollector

logger = logging.getLogger(__name__)

MIN_INTERVAL_S = 300  # vROps recalcule ses métriques toutes les 5 minutes

# Clés de statistiques vROps 8.x (adapterKind VMWARE) → champ du dashboard.
# Plusieurs clés peuvent alimenter le même champ : la première renvoyée (dans l'ordre) l'emporte.
# Le nom d'une métrique dépend du type d'objet (ex. l'usage CPU d'un cluster n'est pas
# cpu|usage_average mais cpu|capacity_usagepct_average, vérifié sur vROps 8.10.2).
CLUSTER_KEYS = {
    "cpu|capacity_usagepct_average": "cpu_usage_pct",
    "cpu|usage_average": "cpu_usage_pct",
    "cpu|demandPct": "cpu_demand_pct",
    "mem|usage_average": "ram_usage_pct",
    "OnlineCapacityAnalytics|timeRemaining": "time_remaining_days",
    "OnlineCapacityAnalytics|cpu|demand|timeRemaining": "cpu_time_remaining_days",
    "OnlineCapacityAnalytics|mem|demand|timeRemaining": "ram_time_remaining_days",
    "OnlineCapacityAnalytics|diskspace|demand|timeRemaining": "disk_time_remaining_days",
    "OnlineCapacityAnalytics|capacityRemainingPercentage": "capacity_remaining_pct",
    "badge|health": "health",
    "badge|risk": "risk",
    "badge|efficiency": "efficiency",
    "summary|number_running_vms": "running_vms",
    "summary|total_number_hosts": "hosts",
}
HOST_KEYS = {
    "cpu|usage_average": "cpu_usage_pct",
    "mem|usage_average": "ram_usage_pct",
    "badge|health": "health",
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
    "cpu|usage_average": "cpu_usage_pct",
    "mem|usage_average": "ram_usage_pct",
    "cpu|readyPct": "cpu_ready_pct",
    "cpu|costopPct": "cpu_costop_pct",
    "mem|balloonPct": "ram_balloon_pct",
    "virtualDisk|totalLatency": "disk_latency_ms",
    "datastore|totalLatency": "disk_latency_ms",
    "diskspace|snapshot": "snapshot_gb",
}
# Propriétés lues seulement pour les objets affichés (rattachement, état).
OBJ_PROPS = ["summary|parentCluster", "summary|parentVcenter", "summary|parentHost",
             "runtime|connectionState", "runtime|maintenanceState"]

# Seuils de contention (recommandations VMware usuelles).
CONTENTION = {"cpu_ready_pct": 5.0, "cpu_costop_pct": 3.0, "ram_balloon_pct": 1.0, "disk_latency_ms": 20.0}

# Niveaux d'alerte vROps → niveaux du centre d'alertes (avertissements : onglet vRealize seulement).
CENTER_LEVELS = {"CRITICAL": "CRITICAL", "IMMEDIATE": "WARNING"}
LEVELS = ("CRITICAL", "IMMEDIATE", "WARNING", "INFORMATION")
MAX_CENTER_ALERTS = 100
MAX_TAB_ALERTS = 500
MAX_ROWS = 300            # rightsizing
MAX_TOP = 50              # contention, snapshots
CAPACITY_WARN_DAYS = 30
HEALTH_COLORS = ("GREEN", "YELLOW", "ORANGE", "RED", "GREY")
KINDS = {"ClusterComputeResource": "Cluster", "HostSystem": "Hôte", "Datastore": "Datastore",
         "VirtualMachine": "VM"}


class VRopsCollector(BaseCollector):
    _DEFAULT_PORT = 443
    _TIMEOUT = 60
    _PAGE_SIZE = 1000
    _MAX_PAGES = 50          # 50 000 objets par type : garde-fou contre une boucle sans fin
    _STATS_CHUNK = 500       # identifiants par requête stats/latest/query
    _PROPS_CHUNK = 100       # identifiants par requête de propriétés (paramètres d'URL)

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

    def _resources(self, session, kind: str) -> List[dict]:
        """Objets d'un type vSphere : identifiant, nom, couleur de santé vROps."""
        rows = self._paged(session, "resources", "resourceList", {"adapterKind": "VMWARE", "resourceKind": kind})
        return [{"id": r["identifier"], "name": (r.get("resourceKey") or {}).get("name") or r["identifier"],
                 "kind": kind, "color": (r.get("resourceHealth") or "GREY").upper()}
                for r in rows if r.get("identifier")]

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

    def _properties(self, session, ids: Iterable[str]) -> Dict[str, Dict[str, str]]:
        """Propriétés de rattachement (cluster, vCenter, état). Facultatives : en cas d'échec, vide."""
        ids = list(dict.fromkeys(ids))
        out: Dict[str, Dict[str, str]] = {}
        try:
            for i in range(0, len(ids), self._PROPS_CHUNK):
                resp = session.get(f"{self.base}/resources/properties",
                                   params={"resourceId": ids[i:i + self._PROPS_CHUNK]}, timeout=self._TIMEOUT)
                resp.raise_for_status()
                for entry in (resp.json() or {}).get("resourcePropertiesList") or []:
                    props = {p.get("name"): p.get("value") for p in entry.get("property") or []}
                    out[entry.get("resourceId")] = {k: v for k, v in props.items() if k in OBJ_PROPS and v}
        except Exception as exc:
            logger.warning("[vROps] Propriétés indisponibles : %s: %s", type(exc).__name__, exc)
            self.diagnostics.append(f"Propriétés (rattachement cluster / vCenter) indisponibles : {type(exc).__name__}")
        return out

    # ── Assemblage ──────────────────────────────────────────────────────────
    def _fill(self, session, payload: Dict[str, Any]) -> None:
        self.diagnostics: List[str] = []
        objs = {kind: self._resources(session, kind) for kind in KINDS}
        by_id = {o["id"]: o for rows in objs.values() for o in rows}
        missing: List[str] = []

        def table(kind: str, keys: Dict[str, str]) -> List[Dict[str, Any]]:
            rows = objs[kind]
            stats = self._latest_stats(session, [o["id"] for o in rows], keys) if rows else {}
            out, filled = [], set()
            for o in rows:
                values = stats.get(o["id"], {})
                row: Dict[str, Any] = {"id": o["id"], "name": o["name"], "color": o["color"]}
                for key, field in keys.items():
                    if field not in row and key in values:
                        row[field] = round(values[key], 2)
                        filled.add(field)
                out.append(row)
            # Contrôle PAR TYPE d'objet : une clé renvoyée pour les hôtes ne prouve rien pour les clusters.
            if rows:
                for field in dict.fromkeys(keys.values()):
                    if field not in filled:
                        candidates = " ou ".join(k for k, f in keys.items() if f == field)
                        missing.append(f"{KINDS[kind]} : {candidates}")
            return out

        clusters = table("ClusterComputeResource", CLUSTER_KEYS)
        hosts = table("HostSystem", HOST_KEYS)
        datastores = table("Datastore", DATASTORE_KEYS)
        vms = table("VirtualMachine", VM_KEYS)

        for ds in datastores:
            cap, used = ds.pop("capacity_gb", None), ds.pop("used_gb", None)
            if cap:
                ds["capacity_tb"] = round(cap / 1024, 2)
                ds["used_tb"] = round((used or 0) / 1024, 2)
                ds["usage_pct"] = round((used or 0) / cap * 100, 1)

        # ── Optimisation ────────────────────────────────────────────────────
        rightsizing = []
        for vm in vms:
            for kind in ("oversized", "undersized", "idle"):
                if vm.get(kind, 0) >= 1:
                    row = {"id": vm["id"], "vm_name": vm["name"], "kind": kind}
                    if kind != "idle":
                        if f"{kind}_vcpus" in vm:
                            row["vcpus"] = int(vm[f"{kind}_vcpus"])
                        if f"{kind}_memory_kb" in vm:
                            row["memory_gb"] = round(vm[f"{kind}_memory_kb"] / 1024 / 1024, 1)
                    rightsizing.append(row)
        rank = {"oversized": 0, "undersized": 1, "idle": 2}
        rightsizing.sort(key=lambda r: (rank[r["kind"]], -(r.get("memory_gb") or 0), r["vm_name"]))
        snapshots = sorted(({"id": v["id"], "vm_name": v["name"], "snapshot_gb": v["snapshot_gb"]}
                            for v in vms if v.get("snapshot_gb", 0) > 0),
                           key=lambda r: r["snapshot_gb"], reverse=True)

        # ── Contention ──────────────────────────────────────────────────────
        def pressure(vm: Dict[str, Any]) -> float:
            """Somme des rapports valeur / seuil des indicateurs dépassés (0 = aucune contention)."""
            return sum(vm[f] / limit for f, limit in CONTENTION.items() if vm.get(f, 0) > limit)
        contention = sorted(
            ({"id": v["id"], "vm_name": v["name"], "score": round(pressure(v), 2),
              **{f: v[f] for f in (*CONTENTION, "cpu_usage_pct", "ram_usage_pct") if f in v}}
             for v in vms if pressure(v) > 0),
            key=lambda r: r["score"], reverse=True)

        # ── Rattachements (seulement pour les objets affichés) ──────────────
        shown_vms = {r["id"] for r in rightsizing[:MAX_ROWS] + contention[:MAX_TOP] + snapshots[:MAX_TOP]}
        props = self._properties(session, [o["id"] for o in clusters + hosts] + sorted(shown_vms))

        def parent(rid: str, key: str) -> Optional[str]:
            return props.get(rid, {}).get(key)

        for c in clusters:
            c["vcenter"] = parent(c["id"], "summary|parentVcenter")
        # Même nom de cluster dans deux vCenter : nom complété pour rester unique (tableaux, historique).
        counts_by_name: Dict[str, int] = {}
        for c in clusters:
            counts_by_name[c["name"]] = counts_by_name.get(c["name"], 0) + 1
        for c in clusters:
            if counts_by_name[c["name"]] > 1:
                c["name"] = f'{c["name"]} ({c.get("vcenter") or c["id"][:8]})'
        for c in clusters:
            by_id[c["id"]]["name"] = c["name"]  # alertes : même nom que dans les tableaux
        for h in hosts:
            h["cluster"] = parent(h["id"], "summary|parentCluster")
            h["connection"] = parent(h["id"], "runtime|connectionState")
            h["maintenance"] = parent(h["id"], "runtime|maintenanceState")
        for row in rightsizing + contention + snapshots:
            row["cluster"] = parent(row["id"], "summary|parentCluster")

        # ── Alertes ─────────────────────────────────────────────────────────
        center, tab_alerts, counts = self._alerts(session, by_id)

        # ── Indicateurs ─────────────────────────────────────────────────────
        remaining = [r["time_remaining_days"] for r in clusters + datastores if r.get("time_remaining_days") is not None]
        total_tb = sum(r.get("capacity_tb", 0) for r in datastores)
        used_tb = sum(r.get("used_tb", 0) for r in datastores)
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
            "vms_contention": len(contention),
            "reclaimable_vcpus": sum(r.get("vcpus", 0) for r in rightsizing if r["kind"] == "oversized"),
            "reclaimable_memory_gb": round(sum(r.get("memory_gb", 0) for r in rightsizing if r["kind"] == "oversized"), 1),
            "snapshots_gb": round(sum(r["snapshot_gb"] for r in snapshots), 1),
        }
        if remaining:
            metrics["capacity_time_remaining_days"] = round(min(remaining), 1)
        if total_tb > 0:
            metrics["datastores_usage_pct"] = round(used_tb / total_tb * 100, 1)

        health_summary = {KINDS[k]: {c: sum(1 for o in rows if o["color"] == c) for c in HEALTH_COLORS}
                          for k, rows in objs.items()}


        status = "HEALTHY"
        days = metrics.get("capacity_time_remaining_days")
        if days is not None and days < CAPACITY_WARN_DAYS:
            center.insert(0, {"severity": "WARNING", "component": "Capacité",
                              "message": f"Saturation prévue par vROps dans {days:g} jour(s)"})
            status = "WARNING"
        if counts["CRITICAL"]:
            status = "WARNING"

        def strip(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            return [{k: v for k, v in r.items() if k != "id" and v is not None} for r in rows]

        payload.update(
            status=status, metrics=metrics, alerts=center,
            clusters_metrics=strip(sorted(clusters, key=lambda r: r["name"])),
            vrops_hosts=strip(sorted(hosts, key=lambda r: (r.get("cluster") or "", r["name"]))),
            vrops_datastores=strip(sorted(datastores, key=lambda r: r.get("usage_pct", -1), reverse=True)),
            rightsizing=strip(rightsizing[:MAX_ROWS]),
            vm_contention=strip(contention[:MAX_TOP]),
            snapshots=strip(snapshots[:MAX_TOP]),
            vrops_alerts=tab_alerts,
            health_summary=health_summary,
            vrops_missing_keys=missing,
            vrops_diagnostics=self.diagnostics,
        )
        logger.info("[vROps] ✓ %d cluster(s), %d hôte(s), %d datastore(s), %d VM(s), %d alerte(s) critique(s)",
                    len(clusters), len(hosts), len(datastores), len(vms), counts["CRITICAL"])

    def _alerts(self, session, by_id: Dict[str, dict]):
        """Alertes actives : sélection pour le centre d'alertes + liste complète pour l'onglet."""
        raw = self._paged(session, "alerts", "alerts", {"activeOnly": "true"})
        counts = {lvl: 0 for lvl in LEVELS}
        center, tab = [], []
        for a in raw:
            level = (a.get("alertLevel") or "").upper()
            if (a.get("status") or "ACTIVE").upper() != "ACTIVE" or level not in counts:
                continue
            counts[level] += 1
            obj = by_id.get(a.get("resourceId")) or {}
            name = a.get("alertDefinitionName") or "Alerte vROps"
            if level in CENTER_LEVELS and len(center) < MAX_CENTER_ALERTS:
                center.append({"severity": CENTER_LEVELS[level], "component": obj.get("name", "vROps"),
                               "message": name + (" (immédiate)" if level == "IMMEDIATE" else "")})
            started = a.get("startTimeUTC")
            tab.append({
                "level": level,
                "impact": (a.get("alertImpact") or "").upper() or None,
                "name": name,
                "object": obj.get("name"),
                "object_kind": KINDS.get(obj.get("kind", ""), None),
                "started_at": datetime.fromtimestamp(started / 1000, timezone.utc).isoformat()
                if isinstance(started, (int, float)) else None,
            })
        order = {lvl: i for i, lvl in enumerate(LEVELS)}
        # Par gravité, puis les plus récentes d'abord.
        tab.sort(key=lambda r: (order[r["level"]], -(datetime.fromisoformat(r["started_at"]).timestamp()
                                                     if r["started_at"] else 0)))
        center.sort(key=lambda r: r["severity"] != "CRITICAL")
        return center, tab[:MAX_TAB_ALERTS], counts
