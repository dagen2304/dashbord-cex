"""
Collecteur Dell PowerStore — API REST PowerStore Manager (HTTPS 443), lecture seule.

Session (guide « PowerStore REST API Reference ») :
    - GET  /api/rest/login_session en Basic Auth → cookie de session + en-tête DELL-EMC-TOKEN
      (jeton anti-CSRF obligatoire sur les POST) ;
    - requêtes suivantes : cookie seul (les identifiants ne sont plus renvoyés) ;
    - POST /api/rest/logout en fin de cycle, même si la collecte échoue.

Métriques (les séries ne sont PAS des collections lisibles en GET : elles sont calculées par
POST /api/rest/metrics/generate {entity, entity_id, interval}) :
    - space_metrics_by_appliance        : capacité physique totale / utilisée, réduction de données ;
    - performance_metrics_by_appliance  : IOPS, latence moyenne, débit.
Alertes : GET /api/rest/alert, état ACTIVE, sévérités Critical / Major / Minor.

Aucune valeur inventée : une métrique illisible reste ABSENTE (jamais 0, jamais la somme des
tailles de volumes, qui est de l'espace promis et non utilisé) et la cause est signalée en INFO.
"""
import logging
from typing import Any, Dict, List, Optional

from app.services.base_collector import BaseCollector

logger = logging.getLogger(__name__)

TB = 1024 ** 4
SEVERITY = {"Critical": "CRITICAL", "Major": "WARNING", "Minor": "WARNING"}
SEVERITY_LABEL = {"Critical": "critique", "Major": "majeure", "Minor": "mineure"}


class PowerStoreCollector(BaseCollector):
    _DEFAULT_PORT = 443
    _TIMEOUT = 60

    def collect(self) -> Dict[str, Any]:
        host = self.config.get("host")
        port = int(self.config.get("port") or self._DEFAULT_PORT)
        self.base = f"https://{host}/api/rest" if port == 443 else f"https://{host}:{port}/api/rest"
        logger.info("[PowerStore] Démarrage collecte pour '%s' (%s)", self.device_name, host)

        payload = self.get_base_payload(status="HEALTHY")
        payload["device_type"] = "storage"
        session = self.http_session()  # politique TLS de l'équipement (verify / pin / insecure)
        session.headers.update({"Content-Type": "application/json", "Accept": "application/json"})
        logged_in = False
        try:
            self._login(session)
            logged_in = True
            self._fill(session, payload)
            return payload
        except Exception as e:
            logger.error("[PowerStore] ✗ Erreur de collecte pour %s : %s", host, e)
            payload["status"] = "OFFLINE"
            self.mark_failure(payload, e)
            payload["alerts"] = [{
                "severity": "CRITICAL",
                "message": f"Connexion REST Dell PowerStore {host}:{port} échouée — {type(e).__name__}: {e}",
                "component": "PowerStore-REST",
            }]
            payload["metrics"] = {}
            return payload
        finally:
            if logged_in:
                self._logout(session, host)
            session.close()

    # ── Session ─────────────────────────────────────────────────────────────
    def _login(self, session) -> None:
        session.auth = (self.config.get("username") or "", self.config.get("password") or "")
        resp = session.get(f"{self.base}/login_session", timeout=self._TIMEOUT)
        resp.raise_for_status()   # 401 = identifiants refusés (collecte suspendue par le planificateur)
        token = resp.headers.get("DELL-EMC-TOKEN")
        if token:
            session.headers["DELL-EMC-TOKEN"] = token
        else:
            logger.warning("[PowerStore] Pas de jeton DELL-EMC-TOKEN : les requêtes POST risquent d'être refusées")
        session.auth = None   # la suite utilise le cookie de session

    def _logout(self, session, host: str) -> None:
        try:
            resp = session.post(f"{self.base}/logout", timeout=15)
            if resp.status_code not in (200, 204):
                logger.warning("[PowerStore] Déconnexion refusée par %s : HTTP %s", host, resp.status_code)
        except Exception as exc:
            logger.warning("[PowerStore] Déconnexion impossible sur %s : %s: %s", host, type(exc).__name__, exc)

    # ── Lecture ─────────────────────────────────────────────────────────────
    def _metrics(self, session, entity: str, entity_id: str, intervals: List[str]) -> Optional[Dict[str, Any]]:
        """Dernier point d'une série calculée par /metrics/generate (None si indisponible)."""
        last_error = None
        for interval in intervals:
            resp = session.post(f"{self.base}/metrics/generate", timeout=self._TIMEOUT,
                                json={"entity": entity, "entity_id": entity_id, "interval": interval})
            if resp.status_code == 200 and isinstance(resp.json(), list) and resp.json():
                return max(resp.json(), key=lambda p: p.get("timestamp") or "")
            last_error = f"HTTP {resp.status_code}"
        self.notes.append(f"{entity} indisponible ({last_error or 'aucun point'})")
        return None

    def _fill(self, session, payload: Dict[str, Any]) -> None:
        self.notes: List[str] = []
        r = session.get(f"{self.base}/appliance", params={"select": "id,name,model"}, timeout=self._TIMEOUT)
        r.raise_for_status()
        appliances = r.json() or []
        if not appliances:
            raise ValueError("aucune appliance renvoyée par l'API")

        # ── Capacité et performance, par appliance puis cumulées ──────────
        total = used = 0.0
        reduction: List[float] = []
        iops = bandwidth = 0.0
        latencies: List[tuple] = []   # (latence µs, IOPS) pour une moyenne pondérée
        space_ok = perf_ok = 0
        rows = []
        for appl in appliances:
            aid = appl["id"]
            row: Dict[str, Any] = {"name": appl.get("name") or aid}
            space = self._metrics(session, "space_metrics_by_appliance", aid, ["Five_Mins", "One_Hour"])
            if space and space.get("physical_total"):
                space_ok += 1
                t, u = float(space["physical_total"]), float(space.get("physical_used") or 0)
                total, used = total + t, used + u
                row.update(capacity_tb=round(t / TB, 2), used_tb=round(u / TB, 2), usage_pct=round(u / t * 100, 1))
                if space.get("data_reduction"):
                    reduction.append(float(space["data_reduction"]))
            perf = self._metrics(session, "performance_metrics_by_appliance", aid, ["Twenty_Sec", "Five_Mins"])
            if perf is not None:
                perf_ok += 1
                a_iops = float(perf.get("total_iops") or 0)
                iops += a_iops
                bandwidth += float(perf.get("total_bandwidth") or 0)
                if perf.get("avg_latency") is not None:
                    latencies.append((float(perf["avg_latency"]), a_iops))
            rows.append(row)

        metrics: Dict[str, Any] = {}
        if space_ok:
            metrics.update(capacity_total_tb=round(total / TB, 2), capacity_used_tb=round(used / TB, 2),
                           capacity_usage_pct=round(used / total * 100, 1))
        if reduction:
            metrics["data_reduction_ratio"] = round(sum(reduction) / len(reduction), 1)
        if perf_ok:
            metrics["iops"] = round(iops)
            metrics["throughput_mb"] = round(bandwidth / 1024 ** 2, 1)   # octets/s → Mo/s
            if latencies:
                weight = sum(w for _, w in latencies)
                avg_us = (sum(l * w for l, w in latencies) / weight) if weight else sum(l for l, _ in latencies) / len(latencies)
                metrics["latency_ms"] = round(avg_us / 1000, 2)                # µs → ms

        # ── Alertes actives ────────────────────────────────────────────────
        alerts: List[Dict[str, Any]] = []
        status = "HEALTHY"
        r_alerts = session.get(f"{self.base}/alert", timeout=self._TIMEOUT, params={
            "select": "id,severity,description_l10n,resource_name,state",
            "state": "eq.ACTIVE", "severity": "in.(Critical,Major,Minor)",
        })
        if r_alerts.status_code == 200:
            for a in r_alerts.json() or []:
                sev = a.get("severity")
                if sev not in SEVERITY:
                    continue
                alerts.append({"severity": SEVERITY[sev], "component": a.get("resource_name") or "Appliance",
                               "message": f"{a.get('description_l10n') or 'Alerte PowerStore'} ({SEVERITY_LABEL[sev]})"})
                status = "CRITICAL" if sev == "Critical" else ("WARNING" if status == "HEALTHY" else status)
        else:
            self.notes.append(f"alertes illisibles (HTTP {r_alerts.status_code})")
        alerts.sort(key=lambda a: a["severity"] != "CRITICAL")

        if self.notes:
            alerts.append({"severity": "INFO", "component": "PowerStore-REST",
                           "message": "Non collecté, laissé vide : " + " ; ".join(self.notes)})

        payload.update(status=status, alerts=alerts, metrics=metrics)
        if len(rows) > 1:
            payload["appliances"] = rows
        logger.info("[PowerStore] ✓ %s", ", ".join(f"{k}={v}" for k, v in metrics.items()) or "aucune métrique")
