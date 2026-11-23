"""
WebSocket temps réel : relaie aux navigateurs les notifications publiées par les workers
sur le canal Redis `noc_updates`.

Authentification : le navigateur ne peut pas envoyer d'en-tête Authorization sur un
WebSocket, le jeton JWT est donc passé en paramètre `?token=`. Il est vérifié AVANT
d'accepter la connexion.
"""
import asyncio
import logging

import redis.asyncio as aioredis
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.redis_client import UPDATES_CHANNEL
from app.core.security import user_from_token

router = APIRouter()
logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        self.active: set[WebSocket] = set()

    async def broadcast(self, message: str) -> None:
        for ws in list(self.active):
            try:
                await ws.send_text(message)
            except Exception:
                self.active.discard(ws)


manager = ConnectionManager()
_listener_task: asyncio.Task | None = None


async def _redis_listener() -> None:
    """Écoute Redis en boucle et se reconnecte en cas de coupure."""
    while True:
        try:
            client = aioredis.from_url(settings.redis_url)
            pubsub = client.pubsub()
            await pubsub.subscribe(UPDATES_CHANNEL)
            logger.info("Listener Redis Pub/Sub démarré.")
            async for message in pubsub.listen():
                if message["type"] == "message":
                    await manager.broadcast(message["data"].decode("utf-8"))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Listener Redis interrompu (%s), reconnexion dans 5 s.", exc)
            await asyncio.sleep(5)


def start_redis_listener() -> None:
    global _listener_task
    if _listener_task is None or _listener_task.done():
        _listener_task = asyncio.get_running_loop().create_task(_redis_listener())


async def stop_redis_listener() -> None:
    global _listener_task
    if _listener_task:
        _listener_task.cancel()
        try:
            await _listener_task
        except (asyncio.CancelledError, Exception):
            pass
        _listener_task = None


def _check_token(token: str) -> None:
    with SessionLocal() as db:
        user_from_token(token, db)


@router.websocket("/updates")
async def websocket_endpoint(websocket: WebSocket, token: str = ""):
    try:
        await run_in_threadpool(_check_token, token)
    except HTTPException:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    manager.active.add(websocket)
    try:
        while True:
            await websocket.receive_text()  # le client envoie des pings ; on les ignore
    except WebSocketDisconnect:
        pass
    finally:
        manager.active.discard(websocket)
