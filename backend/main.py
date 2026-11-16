import os
import time
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.router import api_router
from app.core.database import Base, engine, SessionLocal
from app.models.application import VM
from app.models.inventory import InfrastructureDevice

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="NOC INFRA Central API",
    description="API de supervision en temps réel vCenter et baies de stockage (Unity, Dorado, PowerStore, DataDomain, Scality, NetBackup, Veritas Access)",
    version="1.0.0"
)

# ── Configuration CORS ─────────────────────────────────────────────────────
# Doit être déclaré AVANT include_router pour que les preflight OPTIONS
# soient correctement interceptés.
origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    os.getenv("FRONTEND_URL", "http://localhost:3000")
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def startup_db_setup():
    """
    Crée automatiquement les tables PostgreSQL et ensemence la table des VMs
    avec l'inventaire standard si elle est vide.
    Retente jusqu'à 5 fois avec backoff exponentiel pour absorber les race
    conditions Docker (DB pas encore prête quand le backend démarre).
    """
    max_retries = 5
    for attempt in range(max_retries):
        try:
            # Création des tables relationnelles
            Base.metadata.create_all(bind=engine)
            break  # Succès — sortir de la boucle de retry
        except Exception as e:
            wait = 2 ** attempt  # 1s, 2s, 4s, 8s, 16s
            if attempt < max_retries - 1:
                logger.warning(f"DB non disponible (tentative {attempt + 1}/{max_retries}), nouvelle tentative dans {wait}s : {e}")
                time.sleep(wait)
            else:
                logger.error(f"Impossible de créer les tables après {max_retries} tentatives : {e}")
                return

    # La synchronisation des VMs s'effectue désormais via le collecteur dynamique
    # (vcenter_collector.py) dans le background.

# Inclusion des routes (après les middlewares)
app.include_router(api_router, prefix="/api")

@app.get("/")
async def root():
    return {
        "status": "online",
        "message": "NOC INFRA Central API is operational",
        "supported_components": {
            "virtualization": ["vCenter"],
            "storage_arrays": ["Dell Unity", "Huawei Dorado", "Dell PowerStore"],
            "backup_and_object": ["DataDomain", "Scality S3", "NetBackup", "Veritas Access"]
        }
    }

@app.get("/health")
async def health_check():
    # Ici, nous pourrons étendre le test aux bases PostgreSQL et InfluxDB
    return {"status": "healthy"}
