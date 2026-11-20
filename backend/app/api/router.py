from fastapi import APIRouter, Depends

from app.api.endpoints import (
    alerts, app_endpoints, auth, inventory_endpoints, metrics, reports, vm_diagnostic, ws,
)
from app.core.security import get_current_user

api_router = APIRouter()

# Authentification : /auth/login est public, les autres routes de ce routeur vérifient elles-mêmes la session.
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])

# Toutes les autres routes exigent un utilisateur connecté (rôle viewer minimum).
# Les actions d'écriture ajoutent require_operator / require_admin au niveau de la route.
_authenticated = [Depends(get_current_user)]
api_router.include_router(metrics.router, prefix="/metrics", tags=["metrics"], dependencies=_authenticated)
api_router.include_router(reports.router, prefix="/reports", tags=["reports"], dependencies=_authenticated)
api_router.include_router(vm_diagnostic.router, prefix="/reports/vms", tags=["vm_diagnostic"], dependencies=_authenticated)
api_router.include_router(app_endpoints.router, prefix="/reports/apps", tags=["applications"], dependencies=_authenticated)
api_router.include_router(inventory_endpoints.router, prefix="/inventory", tags=["inventory"], dependencies=_authenticated)
api_router.include_router(alerts.router, prefix="/alerts", tags=["alerts"], dependencies=_authenticated)

# WebSocket : authentifié par le jeton passé en paramètre (?token=), voir ws.py.
api_router.include_router(ws.router, prefix="/ws", tags=["websocket"])
