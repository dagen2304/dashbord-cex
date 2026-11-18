"""
Environnement de test isolé : SQLite (fichier temporaire), Redis simulé (fakeredis),
InfluxDB simulé. Aucun équipement réel n'est contacté.
"""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import fakeredis
import pytest
import redis

_DB_FILE = Path(tempfile.mkdtemp()) / "test.db"
os.environ.update({
    "DATABASE_URL": f"sqlite:///{_DB_FILE}",
    "INFLUXDB_TOKEN": "test-token",
    "SECRET_KEY": "test-secret-key-for-fernet-0123456789abcdef",
    "JWT_SECRET": "test-jwt-secret-different-0123456789abcdef",
    "ADMIN_USERNAME": "admin",
    "ADMIN_INITIAL_PASSWORD": "Admin-Passw0rd!",
    "REDIS_URL": "redis://fake:6379/0",
})

# Un seul serveur Redis simulé partagé par tous les clients créés par l'application.
_FAKE_SERVER = fakeredis.FakeServer()
redis.Redis.from_url = classmethod(lambda cls, *a, **kw: fakeredis.FakeRedis(server=_FAKE_SERVER))  # type: ignore

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.core.influx as influx_module  # noqa: E402

fake_influx = MagicMock()
fake_influx.get_metric_history.return_value = []
influx_module._manager = fake_influx

from fastapi.testclient import TestClient  # noqa: E402

from app.core.database import Base, SessionLocal, engine  # noqa: E402
from app.core.redis_client import r_client  # noqa: E402
from main import app  # noqa: E402
from app.tasks.celery_app import celery_app  # noqa: E402

# Tâches publiées pendant les tests : transport en mémoire, aucun courtier réseau.
celery_app.conf.broker_url = "memory://"

ADMIN = ("admin", "Admin-Passw0rd!")


@pytest.fixture(autouse=True)
def _clean_state():
    """Base et Redis vides avant chaque test ; le compte admin initial est recréé."""
    Base.metadata.drop_all(bind=engine)
    r_client.flushall()
    fake_influx.reset_mock()
    fake_influx.get_metric_history.return_value = []
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:  # déclenche le lifespan : tables + admin initial
        yield c


def login(client, username: str, password: str) -> dict:
    res = client.post("/api/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


@pytest.fixture
def admin_headers(client):
    return login(client, *ADMIN)


def make_user(client, admin_headers, username: str, role: str) -> dict:
    password = "User-Passw0rd!x"
    res = client.post("/api/auth/users", headers=admin_headers,
                      json={"username": username, "password": password, "role": role})
    assert res.status_code == 201, res.text
    return login(client, username, password)


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session
