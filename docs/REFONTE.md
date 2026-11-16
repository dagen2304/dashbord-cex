# Refonte NOC Dashboard — v3 web

*16/11/2026 — remplace le plan Flutter Windows du 26/10/2026.*

## Choix de la cible

L'audit du 26/10 était juste sur le diagnostic, mais il proposait une application **Flutter Windows**, avec réécriture complète en 14 semaines. Nous gardons une **application web** :

- **Accès sans installation.** Le dashboard reste accessible depuis n'importe quel navigateur et sur les écrans muraux du NOC, à la même URL (`cex-dashboard.adcm.orangecm`). Pas de paquet MSIX à déployer sur chaque poste.
- **Technologies conservées.** L'équipe garde ce qu'elle maîtrise : Python, FastAPI, React.
- **Corrections livrées tout de suite.** Le backend est corrigé sur place au lieu d'être réécrit. Les changements plus lourds (TimescaleDB, Vault, SSO) restent possibles plus tard, et seulement s'ils sont justifiés.

Les numéros S…, C… et I… renvoient à l'audit initial, conservé dans l'historique git.

---

## Lot 1 — livré

### Sécurité

| Audit | Correction |
|---|---|
| S2, S3 | **Authentification JWT obligatoire** sur toute l'API et le WebSocket. Trois rôles : `viewer` (lecture), `operator` (exports, applications, acquittement des alertes) et `admin` (équipements, paramètres, utilisateurs). Mots de passe hachés avec bcrypt, longueur minimale 12. Verrouillage du compte après 5 échecs en 15 min. Changer le mot de passe ou le rôle invalide les sessions déjà ouvertes. |
| S4 | **Plus aucune injection Flux.** Les valeurs saisies par l'utilisateur passent dans un enregistrement `params` déclaré en tête de requête, échappé strictement (guillemets, `\`, interpolation `${…}`, caractères de contrôle). Les noms de champs sont filtrés par liste blanche. Testé contre un vrai InfluxDB 2.7. *InfluxDB OSS refuse le paramètre `params` de l'API, d'où cette déclaration dans la requête elle-même.* |
| S5 | `SECRET_KEY` chiffre les mots de passe des équipements, et un `JWT_SECRET` **distinct** signe les sessions. Le backend refuse de démarrer si l'un des deux est absent, faible ou identique à l'autre. |
| S7 | Plus aucun mot de passe ni jeton par défaut dans le code. Toute la configuration passe par un objet `Settings` unique. |
| S8 | **L'API ne contacte plus jamais les équipements.** Elle se contente de lire Redis et les bases. |
| S9 | Détection automatique réservée aux administrateurs. La sonde n'envoie aucun identifiant, et l'adresse est validée (IP ou nom d'hôte uniquement). |
| S11 | Exports CSV et Excel protégés contre l'injection de formules (préfixe `'`). |
| S12 | **Journal d'audit** des connexions, échecs de connexion, modifications d'équipements, de paramètres, d'utilisateurs et d'applications, et des exports. Consultable par un administrateur. |
| — | Nginx devient le point d'entrée unique, avec une CSP stricte (`script-src 'self'`), `X-Frame-Options: DENY` et pas de journalisation des URL `/api` (le jeton du WebSocket y figure). PostgreSQL, Redis, InfluxDB et l'API ne sont plus exposés sur l'hôte. |

### Données fiables

| Audit | Correction |
|---|---|
| C1, C2 | **Aucune valeur inventée.** Supprimés : les latences aléatoires, les mini-courbes aléatoires, le mode démo, l'inventaire de VMs statique, la détection d'équipement par la fin de l'IP, l'OS et l'IP par défaut. Une donnée absente s'affiche « — » ou « Sans données ». |
| — | **Données périmées signalées.** Une collecte qui date de plus de 3 intervalles est marquée « Périmé ». |
| I5 | Un nouvel équipement apparaît « En attente » jusqu'à sa première collecte, et non plus « En ligne ». |
| I4 | Si un mot de passe ne se déchiffre pas, l'équipement passe en erreur, avec ce message, au lieu de tenter une connexion sans mot de passe (risque de verrouillage du compte de service). |
| C10 | Pas de projection de saturation pour le CPU et la RAM. Une projection au-delà de 10 ans est affichée « Stable ». |
| I7 | Plus de division par zéro dans l'analyse capacitaire. |

### Collecte

| Audit | Correction |
|---|---|
| C5, C6, C9 | **Planification unique.** Un répartiteur passe toutes les 15 s et lance **une tâche par équipement**, avec un verrou Redis : deux collectes d'un même équipement ne peuvent plus se chevaucher, et un équipement lent ne bloque plus les autres. L'intervalle réglé dans l'interface s'applique sans redémarrage. Modifier un équipement relance sa collecte immédiatement. |
| C7 | Un seul registre de collecteurs (`app/services/collectors.py`). |
| I2 | Le dernier état est conservé au moins 1 h, avec l'heure de collecte : plus d'équipement qui « disparaît » quand une collecte prend du retard. |
| C3 | Historique capacitaire SQL limité à un point par heure, au lieu d'un par minute sans limite. |
| I6 | Les rapports capacitaires s'appuient sur une seule requête couvrant 60 jours, au lieu d'une requête par ressource sur tout l'historique. |
| I9 | Les e-mails d'alerte partent dans une tâche séparée, avec nouvelles tentatives en cas d'échec. Les identifiants SMTP sont lus dans l'environnement, jamais en base. |
| I10 | Celery ne stocke plus les résultats (plusieurs Mo par vCenter) dans Redis. |
| I11 | Le WebSocket est authentifié et son écouteur Redis se reconnecte automatiquement. |

### Socle technique

| Audit | Correction |
|---|---|
| I1 | Plus aucun appel bloquant dans une route `async`. |
| C12 | Migrations idempotentes au démarrage (`app/core/migrations.py`) pour les colonnes ajoutées aux bases existantes. |
| — | **Pilote PostgreSQL : `psycopg` v3.** SQLAlchemy 2.1 l'utilise par défaut pour les URL `postgresql://` ; avec `psycopg2`, le backend ne démarrait pas. |
| I14 | Image Python 3.12 construite en deux étapes, sans outils de compilation dans l'image finale. Dépendances figées à la version exacte. |
| C14 | **31 tests automatisés** : authentification, rôles, verrouillage, inventaire, temps réel, planificateur, échappement Flux, exports, analyse capacitaire. |
| I17 | Fichiers parasites supprimés : scripts ponctuels, `celery.log`, `celerybeat-schedule`, `graphify-out/`. |

### Interface (réécrite de zéro)

Les sources React de la v2 (`frontend/src`) étaient perdues. L'interface est réécrite avec React 18, TypeScript, Vite, Tailwind, TanStack Query et Recharts. L'API est appelée sur la même origine (`/api`), ce qui corrige I12 (« localhost:8000 en production »).

Design : skill **UI UX Pro Max** (style « Minimalism & Swiss », densité tableau de bord) et règles **dataviz**. Palette des courbes validée pour les daltoniens sur le fond sombre. Accent : orange Orange `#FF7900`. Polices Fira Sans et Fira Code hébergées localement, car le réseau interne peut bloquer Google Fonts. Thèmes sombre (par défaut) et clair. Utilisable sur mobile. Un statut est toujours signalé par une icône et un libellé, jamais par la couleur seule.

| Écran | Contenu |
|---|---|
| Connexion | Compatible avec les gestionnaires de mots de passe ; message d'erreur et verrouillage. |
| Vue d'ensemble | Compteurs (sains, attention, critiques, sans données). Cartes par catégorie, équipements en défaut en premier. Mini-courbe sur 6 h, alerte principale, heure de collecte et mention « périmé ». Mise à jour en temps réel par WebSocket. |
| Détail d'un équipement | Alertes, toutes les métriques, historique (6 h à 30 j) avec seuils, hôtes ESXi, datastores. |
| Alertes | Alertes en cours ; dépassements de latence avec acquittement (opérateur). |
| Machines virtuelles | Saturation CPU/RAM (seuil, durée, période) avec courbe par VM ; systèmes de fichiers invités ; latence lecture/écriture. |
| Applications | Regroupement des VMs par application métier, consommation agrégée (opérateur pour modifier). |
| Capacité | Tendances semaine et mois, projection de saturation, historique, exports CSV et Excel (opérateur). |
| Explorateur | Historique de n'importe quelle métrique de VM ou de datastore. |
| Inventaire | Ajout, modification, archivage et détection (administrateur). Dernière erreur de collecte. Mot de passe jamais réaffiché. |
| Paramètres | Collecte, seuils, SMTP ; utilisateurs et rôles ; journal d'audit (administrateur). |
| Mon compte | Profil et changement de mot de passe. |

### Vérifications effectuées

- `pytest` : 31 tests au vert.
- Toutes les requêtes Flux testées contre un **vrai InfluxDB 2.7**, valeurs piégées comprises (`"`, `${…}`, retours à la ligne).
- Pile complète montée localement : PostgreSQL 15, Redis, InfluxDB 2.7, API avec 4 workers, worker et Beat Celery, Nginx avec la configuration du dépôt. Parcours de toutes les pages avec Playwright, en thèmes sombre et clair, sur bureau et mobile : aucune erreur JavaScript, aucune violation de CSP, aucune erreur 5xx, aucun débordement horizontal sur mobile. Export CSV vérifié.
- Collecte réelle d'un équipement injoignable : l'équipement passe en `OFFLINE` et le message d'erreur s'affiche.

**Non vérifié** (faute d'accès au réseau Orange) : la collecte contre de vrais vCenter, Dorado, Unity, PowerStore, DataDomain, NetBackup, Scality et Veritas, ainsi que la construction des images Docker (le proxy de l'environnement de développement bloque les téléchargements de paquets dans `docker build`).

---

## Mise en production

> **Prérequis : la phase 0 de `docs/ROTATION_SECRETS.md`** (changement des mots de passe des équipements et des secrets de la plateforme), si ce n'est pas déjà fait.

1. **Ajouter les nouveaux secrets.**
   - `JWT_SECRET` : obligatoire et différent de `SECRET_KEY`. Sans lui, le backend refuse de démarrer.
   - `ADMIN_INITIAL_PASSWORD` : sert à créer le premier compte administrateur.
   - `INTERNAL_API_KEY` n'est plus utilisé : vous pouvez le supprimer.
2. **Garder la même `SECRET_KEY`.** Si vous la changez, suivez la procédure de rotation : sinon, les mots de passe des équipements deviennent indéchiffrables.
3. **Construire et pousser les images**, en leur donnant une étiquette de version plutôt que `:latest`.
4. **Déployer, puis se connecter** avec `admin` et `ADMIN_INITIAL_PASSWORD`. Changer immédiatement ce mot de passe dans « Mon compte », puis vider la variable.
5. **Créer les comptes nominatifs** des opérateurs dans Paramètres → Utilisateurs.
6. **Vérifier l'inventaire.** Les équipements dont le mot de passe ne se déchiffre pas sont signalés en erreur : ressaisir leur mot de passe.

Les tables existantes sont conservées. Les nouvelles (`users`, `audit_events`) et la colonne `last_sync_error` sont créées automatiquement au démarrage.

---

## Lot 2 — reste à faire

Par ordre de priorité :

1. **Latence réelle des datastores et des VMs** (vCenter `PerformanceManager`, compteurs `datastore.totalReadLatency.average` et `virtualDisk.*Latency`). Tant que ce n'est pas fait, aucune alerte de latence ne se déclenche, puisque la valeur aléatoire a été supprimée.
2. **Vérification TLS des équipements** (S10) : autorité de certification interne ou certificat épinglé par équipement, et fichier `known_hosts` pour SSH (Veritas). Aujourd'hui, `verify=False` est toujours utilisé dans les collecteurs.
3. **Connexion par l'annuaire d'entreprise** (LDAP/AD ou SSO OIDC) pour remplacer les comptes locaux, en gardant les rôles.
4. **Kubernetes** (S6, I13) : TLS sur l'Ingress, PostgreSQL en `StatefulSet` ou en base managée, `NetworkPolicy`, images à version fixe, `ExternalSecrets`.
5. **Tests des collecteurs sur des réponses réelles enregistrées puis anonymisées**, et validation des unités constructeur par constructeur (Dorado `performancestatistictask`, Unity `currBandwidth` et `currLatency`).
6. **Déconnexion Unity** (I15) : `POST /api/types/loginSessionInfo/action/logout`.
7. **Alembic** à la place des migrations au démarrage, dès la prochaine évolution du schéma.
8. **Nettoyage** : la table `anomalies_saturation` n'est jamais remplie (la saturation est calculée à la volée dans InfluxDB) ; la supprimer.
9. TimescaleDB à la place d'InfluxDB : seulement si la volumétrie ou les besoins de requêtes le justifient.
