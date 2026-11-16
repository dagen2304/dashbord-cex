import os
from celery import Celery

# Initialisation de l'application Celery
# Nommé celery_app (et non 'celery') pour ne pas masquer le module importé
celery_app = Celery(
    "noc_tasks",
    broker=os.environ.get("REDIS_URL", "redis://redis:6379/0"),
    backend=os.environ.get("REDIS_URL", "redis://redis:6379/0"),
    include=['app.tasks.scheduler']
)

# Configuration additionnelle
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Europe/Paris",
    enable_utc=True,
    beat_schedule={
        'polling_infra_60s': {
            'task': 'app.tasks.scheduler.poll_infrastructure_metrics',
            'schedule': 60.0,
        },
    }
)
