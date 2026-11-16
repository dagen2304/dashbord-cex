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
    "CREATE INDEX IF NOT EXISTS ix_metrics_history_device_ts ON metrics_history (device_id, resource_name, timestamp)",
]


def run_migrations(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as conn:
        for stmt in _STATEMENTS:
            conn.execute(text(stmt))
    logger.info("Migrations de schéma appliquées (%d instructions).", len(_STATEMENTS))
