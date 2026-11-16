# NOC INFRA Central Dashboard

Une plateforme moderne de supervision NOC (Network Operations Center) en temps réel pour monitorer un environnement d'infrastructure hétérogène : virtualisation (vSphere vCenter) et baies de stockage/sauvegarde (Dell Unity, Huawei Dorado, Dell PowerStore, DataDomain, Scality, NetBackup, Veritas Access).

---

## 🛠️ Stack Technique

* **Backend** : FastAPI (Python 3.10), Celery (gestion des tâches périodiques), Redis (broker/cache).
* **Frontend** : React.js (Vite), TypeScript, Tailwind CSS, Shadcn/ui (UI Premium), Recharts (Graphiques de charge/latence).
* **Bases de données** : 
  * **PostgreSQL** : Modèles relationnels (inventaire, utilisateurs, rôles, alertes historisées).
  * **InfluxDB 2.x** : Base de données de séries temporelles pour les métriques de performance brutes (IOPS, Latence, Débit, CPU/MEM).

---

## 📂 Structure du Projet

```
v2-dashboard/
├── docker-compose.yml           # Orchestration PRODUCTION
├── docker-compose.dev.yml       # Override DÉVELOPPEMENT (hot-reload, ports DB)
├── .env.example                 # Variables d'environnement (template)
├── README.md                    # Documentation globale
├── backend/                     # Service API et collecteurs
│   ├── Dockerfile               # Image production (non-root, healthcheck, 4 workers)
│   ├── requirements.txt         # Dépendances Python
│   ├── main.py                  # Point d'entrée FastAPI
│   └── app/                     # Code de l'application
│       ├── core/                # Configuration, Postgres, InfluxDB, Celery
│       ├── api/                 # Contrôleurs et Routes FastAPI
│       ├── models/              # Tables PostgreSQL (SQLAlchemy)
│       ├── schemas/             # Validation Pydantic
│       ├── services/            # Collecteurs et SDK (pyVmomi, REST)
│       └── tasks/               # Tâches asynchrones Celery
└── frontend/                    # Interface utilisateur web
    ├── Dockerfile               # Image production (multi-stage → Nginx)
    ├── Dockerfile.dev           # Image développement (Vite hot-reload)
    ├── nginx.conf               # Configuration Nginx (SPA, gzip, cache)
    ├── package.json             # Dépendances Node (React, Recharts)
    └── src/                     # Code source React
        ├── components/          # Composants UI (Shadcn, Graphiques)
        ├── hooks/               # WebSockets, HTTP Custom Hooks
        ├── store/               # Zustand global state
        └── types/               # Interfaces TypeScript
```

---

## 🚀 Démarrage Rapide

### Prérequis

* **Docker** et **Docker Compose** (v2+) installés sur votre machine.

### Étape 1 : Configuration des variables d'environnement

```bash
cp .env.example .env
```

Éditez le fichier `.env` et renseignez vos mots de passe et clés secrètes :

```bash
# Générer une clé secrète JWT
python -c "import secrets; print(secrets.token_hex(32))"
```

> ⚠️ **Ne jamais commiter le fichier `.env` dans Git.**

### Étape 2 : Lancement

#### 🔧 Mode Développement (hot-reload, ports DB exposés)

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

| Service | URL | Description |
|---|---|---|
| **Frontend** | http://localhost:3000 | Vite dev server (hot-reload) |
| **API Swagger** | http://localhost:8000/docs | Documentation interactive FastAPI |
| **InfluxDB** | http://localhost:8086 | Console d'administration |
| **PostgreSQL** | `localhost:5432` | Accès direct BDD |
| **Redis** | `localhost:6379` | Accès direct broker |

#### 🚀 Mode Production

```bash
docker compose up -d --build
```

| Service | URL | Description |
|---|---|---|
| **Frontend** | http://localhost:80 | Nginx (fichiers statiques optimisés) |
| **API** | http://localhost:8000 | Uvicorn multi-worker |

> En production, les ports PostgreSQL, Redis et InfluxDB ne sont **pas** exposés sur l'hôte — accessibles uniquement sur le réseau Docker interne.

### Étape 3 : Vérification

```bash
# Vérifier l'état de tous les conteneurs
docker compose ps

# Vérifier les healthchecks
docker inspect --format='{{.State.Health.Status}}' noc_backend noc_postgres noc_redis noc_influxdb

# Tester l'API
curl http://localhost:8000/health
```

---

## 🏗️ Architecture Docker

### Services

| Service | Image | Rôle | Healthcheck |
|---|---|---|---|
| `db` | `postgres:15-alpine` | Base relationnelle (inventaire, users) | `pg_isready` |
| `influxdb` | `influxdb:2.7-alpine` | Métriques temps réel (IOPS, latence) | `curl /health` |
| `redis` | `redis:7-alpine` | Broker Celery + cache | `redis-cli ping` |
| `backend` | Custom (Python 3.10) | API FastAPI (4 workers, non-root) | `curl /health` |
| `celery_worker` | Custom (Python 3.10) | Exécution des tâches de polling | — |
| `celery_beat` | Custom (Python 3.10) | Planificateur de polling récurrent | — |
| `frontend` | Custom (Nginx / Node) | Interface web React | `wget /` |

### Sécurité Production

* **Utilisateur non-root** : le backend s'exécute sous `appuser` (UID 1001).
* **Ports fermés** : seuls le frontend (80) et le backend (8000) sont exposés en prod.
* **Healthchecks** : tous les services critiques sont monitorés et redémarrés automatiquement.
* **Limites mémoire** : chaque service a un plafond RAM configuré via `deploy.resources.limits`.
* **Headers sécurité** : Nginx ajoute `X-Frame-Options`, `X-Content-Type-Options`, `X-XSS-Protection`.

---

## 📡 Liste des Collecteurs Intégrés

1. **Virtualisation** : Connecteur `pyvmomi` pour extraire la charge CPU/MEM des hyperviseurs et VMs.
2. **Dell Unity** : Client REST API pour récupérer l'état des LUNs, des Storage Pools et la latence.
3. **Huawei Dorado** : Client REST API pour requêter les métriques d'IOPS et de réplication.
4. **Dell PowerStore** : Client REST API pour la télémétrie matérielle et l'espace utilisable.
5. **DataDomain** : Agent SNMP/REST pour collecter les taux de déduplication et l'état des disques.
6. **Scality** : Client REST/S3 pour la volumétrie des Buckets et l'état des nœuds Ring.
7. **Veritas NetBackup** : REST API pour analyser les statuts de jobs de sauvegarde quotidiens.
8. **Veritas Access** : Intégration CLI/REST pour superviser les partages CIFS/NFS.

---

## 🧹 Commandes Utiles

```bash
# Arrêter la stack
docker compose down

# Arrêter la stack ET supprimer les volumes (⚠️ perte des données)
docker compose down -v

# Voir les logs d'un service spécifique
docker compose logs -f backend
docker compose logs -f frontend

# Accéder au shell d'un conteneur
docker exec -it noc_backend sh
docker exec -it noc_postgres psql -U noc_admin -d noc_dashboard

# Reconstruire un seul service
docker compose build backend
docker compose up -d --no-deps backend
```
