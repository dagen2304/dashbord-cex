"""Client Redis partagé (API et workers)."""
import redis

from app.core.config import settings

r_client = redis.Redis.from_url(settings.redis_url, socket_timeout=5, socket_connect_timeout=5)

# Clés Redis utilisées par la plateforme
LATEST_KEY = "noc_latest:{device_id}"      # dernier état collecté d'un équipement
NEXT_POLL_KEY = "noc_next:{device_id}"     # présent tant que le prochain polling n'est pas dû
LOCK_KEY = "noc_lock:{device_id}"          # verrou : une seule collecte à la fois par équipement
HISTORY_GATE_KEY = "noc_hist:{device_id}"  # limite l'historique capacitaire SQL à 1 point / heure
UPDATES_CHANNEL = "noc_updates"            # pub/sub vers les WebSockets
