import asyncio
import os
import json
import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import redis.asyncio as redis

router = APIRouter()
logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

# Gestionnaire de connexions WebSocket
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"Nouveau client WS connecté. Total: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"Client WS déconnecté. Total: {len(self.active_connections)}")

    async def broadcast(self, message: str):
        for connection in list(self.active_connections):
            try:
                await connection.send_text(message)
            except Exception as e:
                logger.error(f"Erreur WS broadcast: {e}")
                self.disconnect(connection)

manager = ConnectionManager()

# Background task to listen to Redis Pub/Sub
async def redis_listener():
    try:
        redis_client = redis.from_url(REDIS_URL)
        pubsub = redis_client.pubsub()
        await pubsub.subscribe("noc_updates")
        
        logger.info("Démarrage du listener Redis Pub/Sub pour les WebSockets...")
        async for message in pubsub.listen():
            if message["type"] == "message":
                data = message["data"].decode("utf-8")
                # Diffuser le message à tous les clients connectés
                await manager.broadcast(data)
    except Exception as e:
        logger.error(f"Erreur dans le listener Redis: {e}")

# Note: Ideally, `redis_listener` is started on app startup.
# We will start it lazily on the first WS connection or in main.py.
listener_task = None

@router.websocket("/updates")
async def websocket_endpoint(websocket: WebSocket):
    global listener_task
    
    # Démarrer le listener si ce n'est pas fait
    if listener_task is None:
        loop = asyncio.get_running_loop()
        listener_task = loop.create_task(redis_listener())

    await manager.connect(websocket)
    try:
        while True:
            # Maintenir la connexion ouverte (on peut aussi utiliser receive_text pour des ping/pong)
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
