from fastapi import APIRouter
from app.api.endpoints import metrics, reports, vm_diagnostic, app_endpoints, inventory_endpoints, ws

api_router = APIRouter()

# Associer le sous-routeur de métriques sous le préfixe /metrics
api_router.include_router(metrics.router, prefix="/metrics", tags=["metrics"])

# Associer le sous-routeur de rapports capacitaires sous le préfixe /reports
api_router.include_router(reports.router, prefix="/reports", tags=["reports"])

# Associer le sous-routeur de diagnostic de saturation des VMs
api_router.include_router(vm_diagnostic.router, prefix="/reports/vms", tags=["vm_diagnostic"])

# Associer le sous-routeur de gestion et métriques des Applications
api_router.include_router(app_endpoints.router, prefix="/reports/apps", tags=["applications"])

# Associer le sous-routeur d'inventaire d'équipements et Auto-Discovery
api_router.include_router(inventory_endpoints.router, prefix="/inventory", tags=["inventory"])

# Associer le sous-routeur WebSocket
api_router.include_router(ws.router, prefix="/ws", tags=["websocket"])

