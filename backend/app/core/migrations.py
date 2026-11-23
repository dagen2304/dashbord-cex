"""
Migrations de schéma minimales et idempotentes.

`Base.metadata.create_all` crée les tables manquantes mais n'ajoute jamais de colonne
à une table existante : les bases de la v2 resteraient sans les nouvelles colonnes.
Ces instructions comblent l'écart. À remplacer par Alembic quand le schéma évoluera davantage.
"""
import logging

from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

_STATEMENTS = [
    "ALTER TABLE infrastructure_devices ADD COLUMN IF NOT EXISTS last_sync_error VARCHAR",
    # Les équipements déjà présents gardent le comportement de la v2 (pas de vérification),
    # signalé dans l'interface ; les nouveaux sont créés en « verify » par l'application.
    "ALTER TABLE infrastructure_devices ADD COLUMN IF NOT EXISTS tls_mode VARCHAR NOT NULL DEFAULT 'insecure'",
    "ALTER TABLE infrastructure_devices ADD COLUMN IF NOT EXISTS tls_fingerprint VARCHAR",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS auth_source VARCHAR(16) NOT NULL DEFAULT 'local'",
    "CREATE INDEX IF NOT EXISTS ix_metrics_history_device_ts ON metrics_history (device_id, resource_name, timestamp)",
    # 3.3.3 — historique faux des PowerStore (< 3.3.3) : la somme des tailles de volumes servait à la
    # fois de capacité et d'utilisé (100 %). Signature exacte de ce défaut : utilisé = capacité.
    # Supprime seulement ces points, pour ne pas fausser la projection de saturation.
    """DELETE FROM metrics_history WHERE resource_name = 'Capacité globale'
         AND capacity_used_tb = capacity_total_tb AND capacity_total_tb > 0
         AND device_id IN (SELECT id FROM infrastructure_devices WHERE device_type = 'DELL_POWERSTORE')""",
]


def run_migrations(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as conn:
        for stmt in _STATEMENTS:
            conn.execute(text(stmt))
    logger.info("Migrations de schéma appliquées (%d instructions).", len(_STATEMENTS))
