from celery import Celery

from app.core.config import settings

# Pas de backend de résultats : les tâches écrivent elles-mêmes dans Redis/InfluxDB/PostgreSQL.
# (La v2 stockait chaque résultat complet — plusieurs Mo pour un vCenter — dans Redis.)
celery_app = Celery("noc_tasks", broker=settings.redis_url, include=["app.tasks.scheduler"])

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    task_ignore_result=True,
    timezone="Europe/Paris",
    enable_utc=True,
    worker_prefetch_multiplier=1,
    task_acks_late=False,
    broker_connection_retry_on_startup=True,
    # Planification UNIQUE : le répartiteur passe toutes les 15 s et lance la collecte
    # des équipements dont l'intervalle (réglable dans l'interface) est écoulé.
    beat_schedule={
        "dispatch-polling": {"task": "app.tasks.scheduler.dispatch_polling", "schedule": 15.0},
    },
)
