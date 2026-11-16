import logging
import re
from typing import Dict, Any, Optional

from app.services.base_collector import BaseCollector

logger = logging.getLogger(__name__)


class VeritasCollector(BaseCollector):
    """
    Collecteur de production pour Veritas Access Storage Appliance.

    Protocole : SSH via paramiko (port 22, ou port configuré)
    Auth      : user/password ou clé privée SSH

    Métriques collectées (CLI Veritas Access) :
        - Capacité totale / utilisée / libre (TB) via `storage list`
        - Nombre de partages NFS actifs via `nfs share list`
        - Nombre de partages CIFS actifs via `cifs share list`
        - Nœuds du cluster en ligne via `cluster status`
        - Statut de santé du système via `system state`
    """

    _DEFAULT_PORT = 22
    _TIMEOUT = 60
    _COMMAND_TIMEOUT = 60  # Timeout par commande SSH (secondes)

    def collect(self) -> Dict[str, Any]:
        logger.info(f"[VeritasAccess] Démarrage collecte pour '{self.device_name}' ({self.config.get('host')})")
        return self._collect_real()

    def _collect_real(self) -> Dict[str, Any]:
        import paramiko

        payload = self.get_base_payload(status="HEALTHY")
        payload["device_type"] = "storage"

        host = self.config.get("host")
        port = int(self.config.get("port", self._DEFAULT_PORT))
        user = self.config.get("username", "")
        password = self.config.get("password", "")

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            logger.info(f"[VeritasAccess] Connexion SSH à {host}:{port} en tant que '{user}'")
            client.connect(
                hostname=host,
                port=port,
                username=user,
                password=password,
                timeout=self._TIMEOUT,
                allow_agent=False,
                look_for_keys=False,
                banner_timeout=self._TIMEOUT,
            )
            logger.info(f"[VeritasAccess] Connexion SSH établie")

            # ── 1. Capacité de stockage ───────────────────────────────────
            total_tb = 0.0
            used_tb = 0.0
            usage_pct = 0.0

            storage_output = self._exec_command(client, "storage list")
            if storage_output:
                total_tb, used_tb, usage_pct = self._parse_storage_capacity(storage_output)

            # ── 2. Partages NFS ───────────────────────────────────────────
            nfs_shares_active = 0
            nfs_output = self._exec_command(client, "nfs share list")
            if nfs_output:
                nfs_shares_active = self._count_active_shares(nfs_output)

            # ── 3. Partages CIFS ──────────────────────────────────────────
            cifs_shares_active = 0
            cifs_output = self._exec_command(client, "cifs share list")
            if cifs_output:
                cifs_shares_active = self._count_active_shares(cifs_output)

            # ── 4. Statut du cluster ──────────────────────────────────────
            cluster_nodes_online = 0
            cluster_nodes_total = 0
            cluster_output = self._exec_command(client, "cluster status")
            if cluster_output:
                cluster_nodes_online, cluster_nodes_total = self._parse_cluster_status(cluster_output)

            # ── 5. État du système ────────────────────────────────────────
            system_state = "HEALTHY"
            state_output = self._exec_command(client, "system state")
            if state_output:
                state_lower = state_output.lower()
                if "degraded" in state_lower or "warning" in state_lower:
                    system_state = "WARNING"
                elif "critical" in state_lower or "fault" in state_lower or "error" in state_lower:
                    system_state = "CRITICAL"

            # ── 6. Alertes ────────────────────────────────────────────────
            alerts = []
            status = system_state

            if cluster_nodes_total > 0 and cluster_nodes_online < cluster_nodes_total:
                status = "CRITICAL" if cluster_nodes_online == 0 else "WARNING"
                alerts.append({
                    "severity": "CRITICAL" if cluster_nodes_online == 0 else "WARNING",
                    "message": f"Nœuds cluster Veritas Access : {cluster_nodes_online}/{cluster_nodes_total} en ligne",
                    "component": "Cluster"
                })

            if usage_pct > 85:
                status = status if status != "HEALTHY" else "WARNING"
                alerts.append({
                    "severity": "WARNING",
                    "message": f"Capacité stockage élevée : {usage_pct}% ({used_tb}/{total_tb} TB)",
                    "component": "Storage"
                })

            payload["status"] = status
            payload["alerts"] = alerts
            payload["metrics"] = {
                "capacity_total_tb": total_tb,
                "capacity_used_tb": used_tb,
                "capacity_usage_pct": usage_pct,
                "nfs_shares_active": nfs_shares_active,
                "cifs_shares_active": cifs_shares_active,
                "cluster_nodes_online": cluster_nodes_online,
                "cluster_nodes_total": cluster_nodes_total,
            }

            logger.info(
                f"[VeritasAccess] ✓ {used_tb}/{total_tb} TB ({usage_pct}%), "
                f"NFS={nfs_shares_active}, CIFS={cifs_shares_active}, "
                f"Nœuds={cluster_nodes_online}/{cluster_nodes_total}"
            )
            return payload

        except Exception as e:
            logger.error(f"[VeritasAccess] ✗ Connexion SSH échouée pour {host}:{port}: {e}")
            payload["status"] = "OFFLINE"
            payload["alerts"] = [{
                "severity": "CRITICAL",
                "message": f"Connexion SSH Veritas Access {host}:{port} échouée — {type(e).__name__}: {e}",
                "component": "SSH"
            }]
            payload["metrics"] = {}
            return payload

        finally:
            try:
                client.close()
            except Exception:
                pass

    # ── Helpers SSH ───────────────────────────────────────────────────────────

    def _exec_command(self, client, command: str) -> Optional[str]:
        """Exécute une commande SSH et retourne stdout, ou None en cas d'erreur."""
        try:
            _, stdout, stderr = client.exec_command(command, timeout=self._COMMAND_TIMEOUT)
            output = stdout.read().decode("utf-8", errors="replace").strip()
            err = stderr.read().decode("utf-8", errors="replace").strip()
            if err:
                logger.debug(f"[VeritasAccess] stderr pour '{command}': {err[:100]}")
            return output
        except Exception as cmd_err:
            logger.warning(f"[VeritasAccess] Commande '{command}' échouée: {cmd_err}")
            return None

    def _parse_storage_capacity(self, output: str):
        """
        Parse la sortie de `storage list` pour extraire les capacités.

        Exemple de sortie typique Veritas Access :
            Storage Name    Total    Used    Avail    Use%
            diskgroup01     50TB     35TB    15TB     70%
        """
        total_bytes = 0.0
        used_bytes = 0.0

        # Regex pour capturer les valeurs avec unités (TB, GB, PB)
        unit_map = {"PB": 1024 ** 5, "TB": 1024 ** 4, "GB": 1024 ** 3, "MB": 1024 ** 2}
        pattern = re.compile(
            r'(?P<name>\S+)\s+'
            r'(?P<total>[\d.]+)\s*(?P<t_unit>[TGPM]B)\s+'
            r'(?P<used>[\d.]+)\s*(?P<u_unit>[TGPM]B)',
            re.IGNORECASE
        )

        for match in pattern.finditer(output):
            t = float(match.group("total")) * unit_map.get(match.group("t_unit").upper(), 1)
            u = float(match.group("used")) * unit_map.get(match.group("u_unit").upper(), 1)
            total_bytes += t
            used_bytes += u

        if total_bytes == 0:
            # Fallback : chercher un pattern de pourcentage
            pct_match = re.search(r'(\d+)%', output)
            if pct_match:
                return 0.0, 0.0, float(pct_match.group(1))
            return 0.0, 0.0, 0.0

        total_tb = round(total_bytes / (1024 ** 4), 2)
        used_tb = round(used_bytes / (1024 ** 4), 2)
        usage_pct = round((used_bytes / total_bytes * 100), 1) if total_bytes > 0 else 0.0
        return total_tb, used_tb, usage_pct

    def _count_active_shares(self, output: str) -> int:
        """
        Compte le nombre de lignes représentant des partages actifs.
        Exclut les lignes d'en-tête (NAME, PATH, etc.).
        """
        count = 0
        for line in output.splitlines():
            stripped = line.strip()
            # Ignorer les en-têtes, lignes vides, et séparateurs
            if not stripped or stripped.startswith("-") or "NAME" in stripped.upper():
                continue
            count += 1
        return count

    def _parse_cluster_status(self, output: str):
        """
        Parse la sortie de `cluster status`.

        Exemple :
            Node    Status    Role
            node01  Online    Primary
            node02  Online    Secondary
            node03  Offline   -
        """
        total = 0
        online = 0
        for line in output.splitlines():
            lower = line.lower()
            if "node" in lower or "online" in lower or "offline" in lower:
                if re.search(r'\bnode\w*\b', lower):
                    total += 1
                    if "online" in lower or "up" in lower or "running" in lower:
                        online += 1
        return online, total
