"""Envoi des e-mails d'alerte. Paramètres lus en base (réglables par un administrateur),
identifiants SMTP lus dans l'environnement (jamais en base, jamais dans l'interface)."""
import logging
import os
import smtplib
from datetime import datetime
from email.message import EmailMessage

from app.core.database import SessionLocal
from app.services.settings_service import get_setting

logger = logging.getLogger(__name__)

SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")


def send_latency_alert_email(device_name: str, target_name: str, latency_ms: float, threshold_ms: float) -> bool:
    with SessionLocal() as db:
        host = get_setting(db, "smtp_server")
        port = int(get_setting(db, "smtp_port") or 25)
        use_tls = get_setting(db, "smtp_use_tls").lower() == "true"
        to = [e.strip() for e in get_setting(db, "alert_email_to").split(",") if e.strip()]
        sender = get_setting(db, "alert_email_from")

    if not to:
        logger.warning("Aucun destinataire d'alerte configuré — e-mail non envoyé.")
        return False

    msg = EmailMessage()
    msg["Subject"] = f"[ALERTE NOC] Latence élevée — {device_name} / {target_name}"
    msg["From"] = sender
    msg["To"] = ", ".join(to)
    msg.set_content(
        "Bonjour,\n\n"
        "Une latence disque supérieure au seuil a été mesurée :\n\n"
        f"- Équipement : {device_name}\n"
        f"- Datastore : {target_name}\n"
        f"- Latence mesurée : {latency_ms:.2f} ms\n"
        f"- Seuil : {threshold_ms:.2f} ms\n"
        f"- Date : {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n\n"
        "— NOC INFRA Dashboard\n"
    )
    with smtplib.SMTP(host, port, timeout=15) as server:
        if use_tls:
            server.starttls()
        if SMTP_USER and SMTP_PASSWORD:
            server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)
    logger.info("Alerte de latence envoyée à %d destinataire(s) pour %s.", len(to), target_name)
    return True
