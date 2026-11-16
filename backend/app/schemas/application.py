from typing import List, Optional
from pydantic import BaseModel, ConfigDict

class VMSchema(BaseModel):
    """
    Validation et sérialisation des Machines Virtuelles.
    """
    id: int
    name: str
    vcenter: str
    cluster: str
    ip: Optional[str] = None
    os: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

class ApplicationBase(BaseModel):
    name: str
    description: Optional[str] = None

class ApplicationCreate(ApplicationBase):
    """
    Payload de création d'une application contenant la liste des IDs des VMs associées.
    """
    vm_ids: List[int]

class ApplicationResponse(ApplicationBase):
    """
    Réponse complète de l'application contenant les VMs associées.
    """
    id: int
    vms: List[VMSchema]

    model_config = ConfigDict(from_attributes=True)
