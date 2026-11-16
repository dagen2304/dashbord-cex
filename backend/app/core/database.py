import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL doit être défini dans l'environnement")

# Configuration de l'engine de connexion PostgreSQL
engine = create_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_recycle=1800
)

# Session locale active
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Classe de base pour les modèles relationnels (SQLAlchemy 2.0)
class Base(DeclarativeBase):
    pass

def get_db():
    """
    Dépendance FastAPI pour obtenir une session de base de données active.
    Ferme proprement la connexion à la fin de la requête.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
