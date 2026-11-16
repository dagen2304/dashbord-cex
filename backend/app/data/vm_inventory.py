"""
Inventaire statique des VMs pour le mode simulation / démo.
Module partagé — référencé à la fois par vm_diagnostic.py et scheduler.py
pour éviter les imports circulaires entre les endpoints et les tâches Celery.
"""

VM_INVENTORY = [
    {"name": "vm-prod-db-01", "vcenter": "vcenter-01", "cluster": "Cluster-PROD-01", "os": "RHEL 8.4", "ip": "10.120.10.11"},
    {"name": "vm-prod-db-02", "vcenter": "vcenter-01", "cluster": "Cluster-PROD-01", "os": "RHEL 8.4", "ip": "10.120.10.12"},
    {"name": "vm-prod-app-01", "vcenter": "vcenter-01", "cluster": "Cluster-PROD-01", "os": "RHEL 8.2", "ip": "10.120.10.21"},
    {"name": "vm-prod-app-02", "vcenter": "vcenter-01", "cluster": "Cluster-PROD-01", "os": "RHEL 8.2", "ip": "10.120.10.22"},
    {"name": "vm-prod-web-01", "vcenter": "vcenter-01", "cluster": "Cluster-PROD-01", "os": "Ubuntu 20.04", "ip": "10.120.10.31"},
    {"name": "vm-prod-web-02", "vcenter": "vcenter-01", "cluster": "Cluster-PROD-01", "os": "Ubuntu 20.04", "ip": "10.120.10.32"},
    {"name": "vm-stage-app-01", "vcenter": "vcenter-01", "cluster": "Cluster-STAGE-01", "os": "RHEL 8.2", "ip": "10.120.20.21"},
    {"name": "vm-stage-web-01", "vcenter": "vcenter-01", "cluster": "Cluster-STAGE-01", "os": "Ubuntu 20.04", "ip": "10.120.20.31"},
    {"name": "vm-dev-worker-01", "vcenter": "vcenter-02", "cluster": "Cluster-DEV-02", "os": "Ubuntu 22.04", "ip": "192.168.50.10"},
    {"name": "vm-dev-worker-02", "vcenter": "vcenter-02", "cluster": "Cluster-DEV-02", "os": "Ubuntu 22.04", "ip": "192.168.50.11"},
    {"name": "vm-sap-prod-01", "vcenter": "vcenter-02", "cluster": "Cluster-SAP-01", "os": "SLES 15 SP2", "ip": "10.130.50.51"},
    {"name": "vm-sap-prod-02", "vcenter": "vcenter-02", "cluster": "Cluster-SAP-01", "os": "SLES 15 SP2", "ip": "10.130.50.52"},
    {"name": "vm-k8s-node-01", "vcenter": "vcenter-02", "cluster": "Cluster-SAP-01", "os": "Ubuntu 20.04", "ip": "10.130.60.11"},
    {"name": "vm-k8s-node-02", "vcenter": "vcenter-02", "cluster": "Cluster-SAP-01", "os": "Ubuntu 20.04", "ip": "10.130.60.12"},
]
