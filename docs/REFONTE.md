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
| C14 | **70 tests automatisés** : authentification, rôles, verrouillage, inventaire, temps réel, planificateur, échappement Flux, exports, analyse capacitaire. |
| I17 | Fichiers parasites supprimés : scripts ponctuels, `celery.log`, `celerybeat-schedule`, `graphify-out/`. |

### Latences disque réelles (vCenter)

La latence était tirée au hasard dans la v2. Elle est maintenant **mesurée** via le `PerformanceManager` de vSphere (`app/services/vsphere_perf.py`) :

| Objet | Compteurs (ms) | Agrégation |
|---|---|---|
| Datastore | `datastore.totalReadLatency.average`, `datastore.totalWriteLatency.average`, relevés sur chaque hôte ESXi | moyenne de la dernière minute (3 échantillons temps réel de 20 s), puis **maximum** entre les hôtes |
| VM | `virtualDisk.totalReadLatency.average`, `virtualDisk.totalWriteLatency.average`, par disque virtuel | moyenne de la dernière minute, puis **disque le plus lent** |

- **Où ça apparaît.** Dans le tableau des datastores du détail vCenter, dans l'onglet « Latence disque » des VMs et dans l'explorateur de métriques. La métrique `latency_ms` du vCenter vaut la pire latence de ses datastores.
- **Alertes.** Un datastore au-dessus du seuil (Paramètres → `latency_threshold_ms`) passe le vCenter en « Attention » et apparaît dans les alertes en cours tant que le dépassement dure. Il est historisé, avec un e-mail au plus une fois par heure et par datastore.
- **Aucune valeur inventée.** Une VM éteinte, un compteur absent ou un échantillon `-1` laissent le champ vide. Si le `PerformanceManager` échoue, le reste de la collecte est conservé et une alerte « Info » le signale.
- **Prérequis côté vCenter.** Aucun réglage n'est nécessaire. Le compte de service en lecture seule suffit, et les compteurs temps réel sont fournis par les hôtes quel que soit le niveau de statistiques de vCenter. Les requêtes sont regroupées par lots de 50 objets, pour rester sous la limite `config.vpxd.stats.maxQueryMetrics`.
- **Vérification.** Tests unitaires avec un faux `PerformanceManager` (résolution des compteurs, agrégation, valeurs manquantes, lots, panne, alertes). **Pas encore vérifié contre un vrai vCenter** : à contrôler lors de la recette sur le réseau Orange.

### Vérification TLS / SSH des équipements (S10)

Dans la v2, tous les collecteurs se connectaient sans vérifier le certificat (`verify=False`, `CERT_NONE`, `AutoAddPolicy`). Une interception aurait suffi à voler les mots de passe des comptes de service. La politique est désormais réglée **équipement par équipement** (`app/services/tls.py`) :

| Mode | HTTPS (vCenter, baies, sauvegarde, Scality) | SSH (Veritas Access) |
|---|---|---|
| **Vérifié par une AC** (défaut des nouveaux équipements) | chaîne de certificats et nom d'hôte vérifiés, avec les AC du système ou l'AC interne `DEVICE_CA_BUNDLE` | — (pas d'AC en SSH) |
| **Empreinte épinglée** | seul le certificat dont l'empreinte SHA-256 est enregistrée est accepté. Adapté aux certificats auto-signés et aux accès par IP | empreinte SHA-256 de la clé d'hôte |
| **Non vérifié** | comportement de la v2, signalé partout | idem |

- **Dans l'inventaire.** Le bouton **« Lire le certificat »** (« Lire la clé SSH » pour Veritas) affiche sujet, émetteur, noms, validité, empreinte, et indique si le certificat serait accepté par une AC. L'administrateur compare l'empreinte avec la console de l'équipement, puis l'épingle en un clic. La lecture est journalisée et n'envoie aucun identifiant.
- **Signalement.** Une colonne « Sécurité » montre le mode de chaque équipement, et un bandeau compte ceux qui ne sont pas vérifiés.
- **Mise à jour sans coupure.** Les équipements existants passent en mode **Non vérifié** pour continuer d'être collectés, mais ils sont signalés. Il faut ensuite les épingler un par un (voir la mise en production).
- **Pour vCenter**, pyVmomi tente d'abord la vérification par AC et ne compare à l'empreinte épinglée qu'en cas d'échec.
- **Restes.** La détection automatique (sonde sans identifiant) ne vérifie pas le certificat. Les collecteurs n'utilisent plus de proxy système implicite (`trust_env=False`).
- **Correctif au passage.** Le port proposé pour Veritas Access est 22 (SSH), et non plus 14161.
- **Tests.** Serveur HTTPS local avec certificat auto-signé : refus en mode AC, acceptation avec l'AC interne, empreinte correcte ou incorrecte, lecture du certificat, clés SSH, règles de l'API.

### Connexion Active Directory

Les opérateurs se connectent avec leur **compte Windows** (`app/core/ldap_auth.py`). La fonction est **désactivée par défaut** (`LDAP_ENABLED=false`).

1. **Recherche.** Un compte de service en lecture seule (`LDAP_BIND_DN`) recherche l'utilisateur par son `sAMAccountName`. L'identifiant est échappé contre l'injection LDAP ; un DN, un UPN ou un caractère spécial sont refusés.
2. **Mot de passe.** Il est vérifié par un **bind LDAP au nom de l'utilisateur** et n'est **jamais stocké** (`password_hash = "!ldap"`). Un mot de passe vide est refusé avant tout appel à l'annuaire, car l'AD accepterait un « bind anonyme ».
3. **Rôle.** Il est déduit des groupes configurés (`LDAP_GROUP_ADMIN`, `LDAP_GROUP_OPERATOR`, `LDAP_GROUP_VIEWER`), **groupes imbriqués compris** grâce à la règle AD `LDAP_MATCHING_RULE_IN_CHAIN`. Si l'utilisateur est dans plusieurs groupes, c'est le rôle le plus élevé qui s'applique. Un utilisateur qui n'est dans aucun de ces groupes est **refusé** (403).
4. **Synchronisation.** Le compte « AD » est créé à la première connexion, puis rôle et nom sont **resynchronisés à chaque connexion**. Un changement de rôle invalide les sessions existantes.
5. **Ce qui reste géré dans le dashboard.** Un administrateur peut désactiver un compte AD. En revanche, son mot de passe, son nom et son rôle ne se modifient que dans l'AD.
6. **Comptes locaux.** Ils restent prioritaires à nom égal. Le compte `admin` local sert de **secours** si l'annuaire est indisponible (réponse 503 explicite dans ce cas).
7. **Chiffrement.** `ldaps://` ou `LDAP_START_TLS=true` est obligatoire, et le certificat des contrôleurs de domaine est vérifié (`LDAP_CA_FILE` pour une AC interne). Plusieurs contrôleurs peuvent être déclarés, avec bascule automatique.
8. **Traçabilité.** Le journal d'audit enregistre la création de comptes AD, les synchronisations de rôle, les refus faute de groupe et les indisponibilités de l'annuaire. Le verrouillage après 5 échecs s'applique aussi aux comptes AD.

**À préparer côté AD :**
- un compte de service en lecture seule ;
- trois groupes, par exemple `NOC-Dashboard-Admins`, `NOC-Dashboard-Operateurs` et `NOC-Dashboard-Lecteurs` ;
- l'AC des contrôleurs de domaine (fichier PEM).

Les variables à renseigner sont dans `.env.example`.

**Vérification.** 11 tests sur un annuaire simulé en mémoire (`ldap3` MOCK_SYNC) couvrent la création du compte, le choix du rôle, les mauvais mots de passe, l'utilisateur sans groupe, le mot de passe vide, l'injection, la priorité du compte local, la resynchronisation, les restrictions et le refus d'une connexion non chiffrée. **Ces tests ne sont pas faits contre le vrai AD**, et la résolution des groupes imbriqués n'est testable qu'avec lui.

### Kubernetes durci (S6, I13)

`k8s-production.yaml` a été réécrit :

| Point | Avant | Après |
|---|---|---|
| **Sécurité des pods** | aucune contrainte, conteneurs en root | namespace en Pod Security Standards **« restricted »** (appliqué) ; tous les conteneurs **non-root**, sans élévation de privilèges, sans capacités, seccomp `RuntimeDefault`, **racine en lecture seule** (`emptyDir` pour `/tmp`), jeton de compte de service non monté |
| **Réseau** | tout ouvert | `NetworkPolicy` **« tout interdit par défaut »**, puis flux minimaux : Ingress → interface et API ; API et workers → bases ; workers → équipements (ports 443, 8088, 3009, 1556, 22) et SMTP ; API → AD (636/389) ; DNS. Les bases n'acceptent que leurs clients et ne sortent nulle part |
| **Secrets** | `envFrom` : **chaque pod recevait tous les secrets**, Redis compris | `secretKeyRef` : chaque conteneur ne reçoit que ses clés (PostgreSQL : son mot de passe ; InfluxDB : les siens ; Redis et l'interface : aucun) |
| **Images** | `:latest`, `imagePullPolicy: Always`, tags flottants | versions figées (`postgres:15.10-alpine`, `influxdb:2.7.11-alpine`, `redis:7.4.1-alpine`, application `3.0.0`, à remplacer par la version publiée, idéalement par son digest) |
| **Bases** | `Deployment` | PostgreSQL et InfluxDB en **`StatefulSet`**, qui **réutilisent les PVC existants** : aucune migration de données |
| **Redis** | volume persistant | cache et broker uniquement : `emptyDir`, persistance désactivée, `maxmemory` |
| **Santé** | aucune sonde | sondes de démarrage, de disponibilité et de vivacité (`/health`, `/ready`, `pg_isready`, `redis-cli ping`, `celery inspect ping`) ; requêtes et limites CPU/mémoire partout |
| **Disponibilité** | — | `PodDisruptionBudget` pour l'API et l'interface ; mises à jour progressives sans indisponibilité ; Beat en `Recreate` (jamais deux planificateurs) |
| **Ingress** | HTTP | **HTTPS uniquement** (secret `noc-tls`, redirection forcée, HSTS d'ingress-nginx), délais adaptés au WebSocket |
| **AC internes** | — | ConfigMap facultative `noc-ca`, montée dans `/certs`, pour `DEVICE_CA_BUNDLE` et `LDAP_CA_FILE` |

- **Interface sans root.** Elle est désormais servie par `nginxinc/nginx-unprivileged` sur le **port 8080**. Le port publié par Docker Compose ne change pas (`HTTP_PORT`, 80 par défaut).

**Vérification :**
- `kubeconform -strict` contre les schémas de Kubernetes 1.30 : 28 objets valides sur 28.
- Contrôle des règles « restricted » sur chaque pod : tous conformes.
- Les images PostgreSQL, Redis, InfluxDB et Nginx ont été lancées sous Docker avec les **mêmes restrictions** (utilisateur non-root, racine en lecture seule, aucune capacité, `no-new-privileges`). Toutes démarrent et répondent, et l'initialisation d'InfluxDB crée bien son bucket.
- **Pas encore appliqué sur un vrai cluster.**

**Mise à jour d'un cluster existant :**
1. **Construire et publier les images** `dagen/v2-dashboard-backend` et `dagen/v2-dashboard-frontend` avec une étiquette de version, puis la reporter dans le manifeste.
2. **Recréer le secret `noc-secrets`** avec les clés listées en tête du manifeste, dont `JWT_SECRET`. Créer aussi le secret TLS `noc-tls`.
3. **Supprimer les anciens `Deployment` `postgres` et `influxdb`** (`kubectl -n noc delete deployment postgres influxdb`). Les PVC sont conservés, et les StatefulSets les reprennent.
4. **Vérifier le namespace du contrôleur Ingress** (`ingress-nginx` par défaut) et les plages d'adresses des équipements, de l'AD et du SMTP dans les `NetworkPolicy`. Le CNI du cluster doit appliquer les `NetworkPolicy` (Calico, Cilium…).
5. **Appliquer** : `kubectl apply -f k8s-production.yaml`. L'ancien PVC `redis-pvc` peut ensuite être supprimé.

### Sessions Dell Unity (I15)

La v2 « fermait » sa session Unisphere par `DELETE …/loginSessionInfo/instances/0`, qui n'existe pas dans l'API. L'échec était masqué, et les sessions restaient ouvertes à chaque collecte, soit une par minute, jusqu'à la limite de la baie. Les identifiants étaient en plus renvoyés à chaque requête.

Désormais, comme le prévoit le guide de l'API REST Unisphere :
1. **Ouverture** : `GET /api/types/loginSessionInfo` en Basic Auth. La baie renvoie un cookie de session et le jeton `EMC-CSRF-TOKEN`.
2. **Requêtes suivantes** : le cookie seul suffit, les identifiants ne sont plus renvoyés.
3. **Fermeture** : `POST /api/types/loginSessionInfo/action/logout` (`localCleanupOnly`, avec le jeton CSRF). Elle est **toujours tentée** dès qu'une session a été ouverte, même si la collecte échoue. Un refus est journalisé.

**Vérification.** 4 tests sur un faux Unisphere : cookie, absence d'identifiants, CSRF, corps de la déconnexion, collecte en échec, connexion refusée, déconnexion refusée. **À confirmer sur une vraie baie** : le compteur de sessions de l'utilisateur de service doit rester stable dans Unisphere.

### vRealize Operations 8.x (Aria Operations)

Nouveau type d'équipement **« VMware vRealize Operations »** (`VROPS`), collecté par la Suite API REST en lecture seule (`backend/app/services/vrops_collector.py`). Écrit pour la version 8.10.2 en service.

- **Rôle** : vROps complète la collecte vCenter, il ne la remplace pas. vCenter reste la source du temps réel (latences toutes les 20 s, état des hôtes). vROps apporte l'analyse : capacité restante et jours avant saturation, scores santé / risque / efficacité par cluster, VMs surdimensionnées, sous-dimensionnées ou inactives, alertes vROps.
- **Session** : jeton obtenu par `POST /suite-api/api/auth/token/acquire`, envoyé dans `Authorization: vRealizeOpsToken …`, puis libéré (`/auth/token/release`) en fin de cycle, même en cas d'échec.
- **Compte** : rôle ReadOnly. Pour un compte d'annuaire déclaré dans vROps, saisir `SOURCE\utilisateur` (`SOURCE` = nom de la source d'authentification dans vROps).
- **Fréquence** : vROps recalcule toutes les 5 minutes ; le répartiteur applique donc 5 minutes minimum à ce type, quel que soit l'intervalle global.
- **Volume** : lecture des dernières valeurs en lot (500 objets par requête), pagination sur `totalCount`. Seule une liste de métriques utiles est collectée (`CLUSTER_KEYS`, `DATASTORE_KEYS`, `VM_KEYS`).
- **Aucune valeur inventée** : une métrique que vROps ne renvoie pas reste vide, et la liste des clés absentes est signalée par une alerte « INFO » sur l'équipement. C'est le premier point à contrôler sur le vrai vROps : si une clé est nommée autrement dans votre version, il suffit de la corriger dans ces listes.
- **Écran** : la fiche de l'équipement affiche les clusters, les datastores selon vROps et les recommandations de redimensionnement (filtrables). Les alertes vROps critiques, immédiates et avertissements rejoignent le centre d'alertes.
- **Détection** : « Détecter » reconnaît vROps par `/suite-api/api/deployment/node/status`, seule ressource publique de la Suite API.
- **Démo** : un vROps fictif est ajouté au mode démonstration, y compris sur une démo déjà initialisée.

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
6. **Épingler les certificats.** Après la mise à jour, les équipements existants sont « Non vérifié ». Pour chacun : Modifier → « Lire le certificat » → comparer l'empreinte avec la console de l'équipement → « Épingler cette empreinte ». Pour Veritas, faire de même avec la clé SSH. Si vous avez une AC interne, montez son fichier PEM et renseignez `DEVICE_CA_BUNDLE` pour utiliser le mode « Vérifié par une AC ».
7. **Vérifier l'inventaire.** Les équipements dont le mot de passe ne se déchiffre pas sont signalés en erreur : ressaisir leur mot de passe.

Les tables existantes sont conservées. Les nouvelles (`users`, `audit_events`) et la colonne `last_sync_error` sont créées automatiquement au démarrage.

---

## Lot 2 — reste à faire

Par ordre de priorité :

1. **Tests des collecteurs sur des réponses réelles enregistrées puis anonymisées**, et validation des unités constructeur par constructeur (Dorado `performancestatistictask`, Unity `currBandwidth` et `currLatency`).
2. **Alembic** à la place des migrations au démarrage, dès la prochaine évolution du schéma.
3. **Nettoyage** : la table `anomalies_saturation` n'est jamais remplie (la saturation est calculée à la volée dans InfluxDB) ; la supprimer.
4. TimescaleDB à la place d'InfluxDB : seulement si la volumétrie ou les besoins de requêtes le justifient.
