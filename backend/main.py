import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.endpoints.ws import start_redis_listener, stop_redis_listener
from app.api.router import api_router
from app.core.config import settings
from app.core import log_redaction
from app.core.database import Base, engine
from app.core.migrations import run_migrations
from app.core.redis_client import r_client
from app.core.security import bootstrap_admin

# Import des modèles pour que create_all connaisse toutes les tables.
from app.models import application, inventory, user  # noqa: F401

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log_redaction.install()  # jetons masqués dans tous les journaux (URL du WebSocket)
logger = logging.getLogger(__name__)


def _init_database() -> None:
    """Crée les tables, applique les migrations et le compte admin initial.
    Retente pour absorber le démarrage plus lent de PostgreSQL sous Docker."""
    for attempt in range(6):
        try:
            Base.metadata.create_all(bind=engine)
            run_migrations(engine)
            bootstrap_admin()
            return
        except Exception as exc:
            wait = 2 ** attempt
            logger.warning("Base indisponible (tentative %d/6) : %s — nouvel essai dans %ds", attempt + 1, exc, wait)
            time.sleep(wait)
    raise RuntimeError("Impossible d'initialiser la base de données")


@asynccontextmanager
async def lifespan(_: FastAPI):
    await run_in_threadpool(_init_database)
    start_redis_listener()
    yield
    await stop_redis_listener()


app = FastAPI(
    title="NOC INFRA Central API",
    description="Supervision vCenter et baies de stockage / sauvegarde.",
    version="3.3.5",
    lifespan=lifespan,
)

# En production le frontend est servi par Nginx sur la même origine que l'API (/api) :
# aucun CORS n'est nécessaire. CORS_ORIGINS sert uniquement au développement.
if settings.cors_origin_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

app.include_router(api_router, prefix="/api")


@app.get("/health", include_in_schema=False)
def health():
    """Vivacité : le processus répond."""
    return {"status": "ok"}


@app.get("/ready", include_in_schema=False)
async def ready():
    """Disponibilité : PostgreSQL et Redis répondent."""
    checks = {}

    def _db():
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))

    for name, fn in (("postgres", _db), ("redis", r_client.ping)):
        try:
            await asyncio.wait_for(run_in_threadpool(fn), timeout=3)
            checks[name] = "ok"
        except Exception as exc:
            checks[name] = f"error: {type(exc).__name__}"
    ok = all(v == "ok" for v in checks.values())
    return JSONResponse(status_code=200 if ok else 503, content={"status": "ok" if ok else "degraded", "checks": checks})
