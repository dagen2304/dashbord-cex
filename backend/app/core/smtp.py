import os
import smtplib
from email.message import EmailMessage
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# Config SMTP via variables d'environnement
SMTP_SERVER = os.getenv("SMTP_SERVER", "localhost")
SMTP_PORT = int(os.getenv("SMTP_PORT", 1025))  # 1025 par défaut pour le dev (ex: MailHog)
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "false").lower() == "true"
ALERT_EMAIL_TO = os.getenv("ALERT_EMAIL_TO", "")
ALERT_EMAIL_FROM = os.getenv("ALERT_EMAIL_FROM", "noc-alerts@orange.com")

def send_latency_alert_email(device_name: str, target_name: str, latency_ms: float, threshold_ms: float):
    """
    Envoie un e-mail d'alerte pour une latence critique détectée sur un Datastore ou une VM.
    """
    # Configuration par défaut
    server_host = SMTP_SERVER
    server_port = SMTP_PORT
    server_user = SMTP_USER
    server_pwd = SMTP_PASSWORD
    use_tls = SMTP_USE_TLS
    email_to = ALERT_EMAIL_TO
    email_from = ALERT_EMAIL_FROM

    # Tentative de lecture dynamique en base de données
    from app.core.database import SessionLocal
    db = SessionLocal()
    try:
        from app.models.inventory import SystemSetting
        
        s_host = db.query(SystemSetting).filter(SystemSetting.key == "smtp_server").first()
        if s_host: server_host = s_host.value
            
        s_port = db.query(SystemSetting).filter(SystemSetting.key == "smtp_port").first()
        if s_port:
            try: server_port = int(s_port.value)
            except ValueError: pass
            
        s_tls = db.query(SystemSetting).filter(SystemSetting.key == "smtp_use_tls").first()
        if s_tls: use_tls = s_tls.value.lower() == "true"
            
        s_to = db.query(SystemSetting).filter(SystemSetting.key == "alert_email_to").first()
        if s_to: email_to = s_to.value
            
        s_from = db.query(SystemSetting).filter(SystemSetting.key == "alert_email_from").first()
        if s_from: email_from = s_from.value
    except Exception as e:
        logger.warning(f"Impossible de lire les paramètres SMTP en base : {e}")
    finally:
        db.close()

    if not email_to:
        logger.warning("Aucun destinataire d'alerte configuré (alert_email_to) — e-mail non envoyé.")
        return False

    try:
        msg = EmailMessage()
        msg['Subject'] = f"[ALERTE NOC] Latence Critique Détectée - {device_name} / {target_name}"
        msg['From'] = email_from
        msg['To'] = email_to

        content = f"""Bonjour,

Une alerte de latence critique a été détectée sur votre infrastructure de virtualisation :

- Équipement (vCenter) : {device_name}
- Cible (Datastore) : {target_name}
- Latence mesurée : {latency_ms:.2f} ms
- Seuil critique configuré : {threshold_ms:.2f} ms
- Date/Heure : {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

Merci de vérifier l'état du stockage.

Cordialement,
NOC Dashboard Alerting System
"""
        msg.set_content(content)

        with smtplib.SMTP(server_host, server_port) as server:
            if use_tls:
                server.starttls()
            if server_user and server_pwd:
                server.login(server_user, server_pwd)
            server.send_message(msg)
            
        logger.info(f"E-mail d'alerte envoyé avec succès à {email_to} pour {target_name} (Latence: {latency_ms:.2f}ms)")
        return True
    except Exception as e:
        logger.error(f"Échec de l'envoi de l'e-mail d'alerte SMTP : {str(e)}")
        return False
