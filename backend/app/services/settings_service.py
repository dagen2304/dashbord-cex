"""Paramètres système stockés en base (table system_settings), avec valeurs par défaut."""
from typing import Dict, List, Tuple

from sqlalchemy.orm import Session

from app.models.inventory import SystemSetting

# clé -> (valeur par défaut, description, catégorie)
DEFAULT_SETTINGS: Dict[str, Tuple[str, str, str]] = {
    "polling_interval": ("60", "Intervalle de collecte des métriques (secondes, minimum 30)", "POLLING"),
    "latency_threshold_ms": ("20.0", "Seuil d'alerte de latence disque (ms)", "ALERTING"),
    "alert_email_to": ("", "Destinataires des alertes e-mail (séparés par des virgules)", "ALERTING"),
    "alert_email_from": ("noc-alerts@orange.com", "Expéditeur des alertes e-mail", "ALERTING"),
    "smtp_server": ("localhost", "Serveur SMTP", "SMTP"),
    "smtp_port": ("25", "Port SMTP", "SMTP"),
    "smtp_use_tls": ("false", "Activer STARTTLS (true/false)", "SMTP"),
}


def ensure_defaults(db: Session) -> List[SystemSetting]:
    """Crée les paramètres manquants (sans écraser les valeurs existantes)."""
    existing = {s.key: s for s in db.query(SystemSetting).all()}
    added = False
    for key, (value, description, category) in DEFAULT_SETTINGS.items():
        if key not in existing:
            s = SystemSetting(key=key, value=value, description=description, category=category)
            db.add(s)
            existing[key] = s
            added = True
    if added:
        db.commit()
    return sorted(existing.values(), key=lambda s: (s.category, s.key))


def get_setting(db: Session, key: str) -> str:
    row = db.get(SystemSetting, key)
    if row is not None:
        return row.value
    return DEFAULT_SETTINGS[key][0]


def get_polling_interval(db: Session) -> int:
    try:
        return max(30, int(float(get_setting(db, "polling_interval"))))
    except (TypeError, ValueError):
        return 60
