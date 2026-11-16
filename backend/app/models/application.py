from sqlalchemy import Table, Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship
from app.core.database import Base

# Table d'association Many-to-Many
association_app_vm = Table(
    "association_app_vm",
    Base.metadata,
    Column("application_id", Integer, ForeignKey("applications.id", ondelete="CASCADE"), primary_key=True),
    Column("vm_id", Integer, ForeignKey("vms.id", ondelete="CASCADE"), primary_key=True)
)

class VM(Base):
    """
    Représente une Machine Virtuelle (VM) dans PostgreSQL.
    """
    __tablename__ = "vms"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)
    vcenter = Column(String, nullable=False)
    cluster = Column(String, nullable=False)
    ip = Column(String, nullable=True)
    os = Column(String, nullable=True)

    # Relation inverse Many-to-Many
    applications = relationship(
        "Application",
        secondary=association_app_vm,
        back_populates="vms"
    )

class Application(Base):
    """
    Représente une Application regroupant plusieurs VMs de différents vCenters/Clusters.
    """
    __tablename__ = "applications"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)
    description = Column(String, nullable=True)

    # Relation Many-to-Many vers les VMs
    vms = relationship(
        "VM",
        secondary=association_app_vm,
        back_populates="applications"
    )
