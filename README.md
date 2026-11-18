# NOC INFRA — Dashboard de supervision (v3)

Supervision centralisée de l'infrastructure : virtualisation (VMware vCenter), stockage (Huawei Dorado, Dell Unity, Dell PowerStore, Veritas Access), sauvegarde (DataDomain, NetBackup) et stockage objet (Scality RING).

Ce qui a changé dans la refonte, et ce qui reste à faire : **[docs/REFONTE.md](docs/REFONTE.md)**.

## Architecture

```
navigateur ──> Nginx (:80) ──/──────> interface React (fichiers statiques)
                    └──────/api────> API FastAPI ──> PostgreSQL (inventaire, utilisateurs, audit, historique capacitaire)
                                         │      └──> InfluxDB  (séries temporelles)
                                         └─────────> Redis     (dernier état, verrous, temps réel)

celery beat ──15 s──> dispatch_polling ──> collect_device(id)  ──> équipements (lecture seule)
                                                 └──> InfluxDB, PostgreSQL, Redis ──pub/sub──> WebSocket
```

- **Seuls les workers Celery contactent les équipements.** L'API lit uniquement les bases et Redis.
- **Une tâche de collecte par équipement**, protégée par un verrou. L'intervalle se règle dans l'interface.
- **Aucune valeur inventée** : une donnée absente ou trop ancienne est affichée comme telle.

## Stack

| | |
|---|---|
| Backend | Python 3.12, FastAPI, SQLAlchemy 2.1 (psycopg 3), Celery, Pydantic Settings |
| Interface | React 18, TypeScript, Vite, Tailwind CSS, TanStack Query, Recharts |
| Données | PostgreSQL 15, InfluxDB 2.7, Redis 7 |
| Sécurité | JWT + rôles (`viewer`, `operator`, `admin`), bcrypt, journal d'audit, CSP |

## Démarrage

```bash
cp .env.example .env
```

Remplacez chaque `CHANGE_ME` dans `.env`. Pour générer une valeur :

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

`SECRET_KEY` et `JWT_SECRET` doivent être **différents**. Le backend refuse de démarrer sinon.

Production :

```bash
docker compose up -d --build
```

Ouvrez `http://<serveur>/` et connectez-vous avec `ADMIN_USERNAME` / `ADMIN_INITIAL_PASSWORD`. Changez ensuite ce mot de passe dans « Mon compte ».

Développement (ports des bases et de l'API exposés, rechargement à chaud du backend) :

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

Interface en développement (Vite relaie `/api` vers `localhost:8000`) :

```bash
cd frontend && npm ci && npm run dev
```

## Tests

```bash
cd backend && python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
```

```bash
cd backend && .venv/bin/python -m pytest tests -q
```

```bash
cd frontend && npm run build
```

## Mode démonstration

Pour un environnement de test ou de formation, `DEMO_DATA=true` crée 9 équipements fictifs : vCenter, baies, sauvegarde et objet, avec leurs VMs, datastores, alertes, saturations et historique capacitaire. Leurs données sont rafraîchies chaque minute par le même pipeline que les vraies collectes.

- Leurs adresses se terminent par `.demo.invalid`, un domaine réservé : la collecte réelle les ignore toujours.
- L'interface les signale par un badge « Démo ».
- Pour les retirer, il suffit de les archiver depuis l'inventaire, puis de repasser `DEMO_DATA=false`.

**Ne jamais l'activer en production.**

## Rôles

| Rôle | Peut |
|---|---|
| Lecteur (`viewer`) | consulter tous les écrans |
| Opérateur (`operator`) | + exporter les rapports, gérer les applications, acquitter les alertes |
| Administrateur (`admin`) | + gérer les équipements, les paramètres, les utilisateurs ; consulter le journal d'audit |

## Vérifications

```bash
docker compose ps
```

```bash
docker compose exec backend curl -fs http://localhost:8000/ready
```

`/health` (vivacité) et `/ready` (PostgreSQL et Redis joignables) ne sont exposés que sur le réseau Docker interne.

## Documentation

- [docs/REFONTE.md](docs/REFONTE.md) : refonte v3, procédure de mise en production, lot 2
- [docs/DEPLOIEMENT_RHEL.md](docs/DEPLOIEMENT_RHEL.md) : **production sur serveur RHEL** (Podman + Quadlet, installation hors ligne, sauvegardes, mises à jour, flux réseau)
- [docs/ROTATION_SECRETS.md](docs/ROTATION_SECRETS.md) : rotation des secrets (phase 0)
