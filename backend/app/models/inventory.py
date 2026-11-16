import base64
import uuid
from datetime import datetime
from hashlib import sha256
from cryptography.fernet import Fernet
from sqlalchemy import Column, String, Integer, Boolean, DateTime, Float, ForeignKey
from sqlalchemy.orm import relationship
from app.core.database import Base

# Clé Fernet dérivée de SECRET_KEY (validée au démarrage par app.core.config).
from app.core.config import settings

fernet_key = base64.urlsafe_b64encode(sha256(settings.secret_key.encode()).digest())
cipher_suite = Fernet(fernet_key)


class PasswordDecryptionError(Exception):
    """Le mot de passe stocké ne peut pas être déchiffré avec la SECRET_KEY actuelle."""

class InfrastructureDevice(Base):
    """
    Modèle SQLAlchemy représentant un équipement d'infrastructure NOC (vCenter, Baies, etc.)
    dans la table PostgreSQL.
    SSOT (Single Source of Truth) : cette table pilote l'ensemble des pipelines de collecte.
    """
    __tablename__ = "infrastructure_devices"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    display_name = Column(String, nullable=False)
    hostname_ip = Column(String, nullable=False)
    device_type = Column(String, nullable=False)      # ex: VCENTER, HUAWEI_DORADO
    category = Column(String, nullable=False)          # ex: VIRTUALIZATION, STORAGE, BACKUP
    port = Column(Integer, nullable=False)
    username = Column(String, nullable=False)
    encrypted_password = Column(String, nullable=False)

    # Cycle de vie & pipeline
    is_active = Column(Boolean, default=True)
    is_archived = Column(Boolean, default=False)       # Soft Delete — conserve l'historique InfluxDB
    last_sync_status = Column(String, default="UNSYNCED")   # ONLINE / OFFLINE / ERROR / UNSYNCED
    last_sync_at = Column(DateTime, nullable=True)     # Horodatage du dernier cycle de polling
    last_sync_error = Column(String, nullable=True)    # Dernière erreur de collecte (affichée dans l'inventaire)

    # Relations de liaison
    discovery_record = relationship("ServiceDiscovery", back_populates="device", uselist=False, cascade="all, delete-orphan")
    metrics_history = relationship("MetricHistory", back_populates="device", cascade="all, delete-orphan")
    anomalies = relationship("AnomalySaturation", back_populates="device", cascade="all, delete-orphan")
    latency_anomalies = relationship("LatencyAnomaly", back_populates="device", cascade="all, delete-orphan")

    def set_password(self, password: str):
        """
        Chiffre symétriquement le mot de passe en UTF-8 via Fernet.
        """
        if password:
            self.encrypted_password = cipher_suite.encrypt(password.encode()).decode()

    def get_password(self) -> str:
        """
        Déchiffre le mot de passe pour usage interne (collecteurs uniquement).
        Lève PasswordDecryptionError plutôt que de renvoyer une chaîne vide :
        une connexion sans mot de passe verrouillerait le compte de service.
        """
        try:
            return cipher_suite.decrypt(self.encrypted_password.encode()).decode()
        except Exception as exc:
            raise PasswordDecryptionError(self.display_name) from exc

class ServiceDiscovery(Base):
    """
    Registre de Découverte de Services (Service Discovery).
    Gère l'inscription et le statut actif des équipements dans les pipelines de polling.
    """
    __tablename__ = "service_discovery"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(String, ForeignKey("infrastructure_devices.id", ondelete="CASCADE"), unique=True, nullable=False)
    registered_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)

    # Relation vers l'équipement
    device = relationship("InfrastructureDevice", back_populates="discovery_record")

class MetricHistory(Base):
    """
    Table de séries temporelles pour les métriques de volumétrie.
    Historise l'espace disque quotidiennement pour l'Analyse Capacitaire.
    """
    __tablename__ = "metrics_history"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(String, ForeignKey("infrastructure_devices.id", ondelete="SET NULL"), nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    resource_name = Column(String, nullable=False, default="Global")
    unit = Column(String, nullable=False, default="TB")
    capacity_total_tb = Column(Float, nullable=False)
    capacity_used_tb = Column(Float, nullable=False)
    capacity_free_tb = Column(Float, nullable=False)
    usage_pct = Column(Float, nullable=False)

    # Relation vers l'équipement
    device = relationship("InfrastructureDevice", back_populates="metrics_history")

class AnomalySaturation(Base):
    """
    Table historisant les anomalies de surcharge prolongée CPU/RAM sur les machines virtuelles.
    Alimente le module Diagnostic de Saturation.
    """
    __tablename__ = "anomalies_saturation"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(String, ForeignKey("infrastructure_devices.id", ondelete="SET NULL"), nullable=True)
    vm_name = Column(String, nullable=False, index=True)
    cluster = Column(String, nullable=False)
    vcenter = Column(String, nullable=False)
    resource = Column(String, nullable=False)          # ex: CPU, RAM
    max_value = Column(Float, nullable=False)          # Pourcentage max atteint lors du pic (> 90)
    duration_minutes = Column(Integer, nullable=False) # Durée continue sous tension (en minutes)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    os = Column(String, nullable=True)
    ip = Column(String, nullable=True)

    # Relation vers l'équipement
    device = relationship("InfrastructureDevice", back_populates="anomalies")

class LatencyAnomaly(Base):
    """
    Table historisant les alertes de latence disque prolongée ou critique (vCenter).
    """
    __tablename__ = "anomalies_latency"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(String, ForeignKey("infrastructure_devices.id", ondelete="SET NULL"), nullable=True)
    target_name = Column(String, nullable=False, index=True)
    latency_ms = Column(Float, nullable=False)
    threshold_ms = Column(Float, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    is_resolved = Column(Boolean, default=False)

    # Relation vers l'équipement
    device = relationship("InfrastructureDevice", back_populates="latency_anomalies")


class SystemSetting(Base):
    """
    Table stockant les paramètres système du NOC (ex: intervalle de polling, seuils).
    """
    __tablename__ = "system_settings"

    key = Column(String, primary_key=True, index=True)
    value = Column(String, nullable=False)
    description = Column(String, nullable=True)
    category = Column(String, nullable=False, default="GENERAL")
