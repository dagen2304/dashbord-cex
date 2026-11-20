# Déploiement de production sur serveur RHEL

Première mise en production sur **un serveur RHEL unique**, en attendant que les flux du cluster Kubernetes soient ouverts. Les **mêmes images** et les **mêmes réglages de sécurité** serviront ensuite sur Kubernetes (`k8s-production.yaml`).

## Principe

| | |
|---|---|
| Moteur | **Podman** (fourni et supporté par Red Hat) avec **Quadlet** : chaque conteneur est un service `systemd` (`systemctl`, `journalctl`), démarré automatiquement au boot |
| Installation | **hors ligne possible** : les images sont construites sur un poste qui a accès à Internet, puis transférées dans un paquet `.tar.gz` |
| Sécurité | conteneurs **non-root**, racine en lecture seule, sans capacités, `no-new-privileges`, SELinux actif ; secrets dans `podman secret`, jamais dans un fichier lisible ; Redis protégé par mot de passe (depuis 3.2.2) |
| Accès | **HTTPS** sur le port 443 du serveur ; le port 80 redirige vers HTTPS |
| Exploitation | sauvegarde quotidienne, mise à jour avec retour arrière automatique, testeur de flux réseau |

```
utilisateurs ──443──> noc-frontend (nginx, TLS) ──> noc-backend (API) ──> noc-postgres / noc-influxdb / noc-redis
                                                    noc-worker (collecte) ──> équipements, SMTP
                                                    noc-beat (planificateur)
                       réseau podman privé « noc » ; seul noc-frontend publie des ports
```

## 1. Prérequis

**Serveur.** RHEL **9.2 ou plus** (recommandé), ou RHEL 8.8 ou plus, avec Podman 4.4 minimum (`dnf install -y podman`).

> **Docker CE et Podman ne peuvent pas cohabiter sur RHEL 8** : le paquet `containerd.io` de Docker remplace `runc`, dont Podman a besoin, et `dnf install podman` échoue. Sur un serveur dédié au NOC, retirez d'abord Docker avec `dnf remove docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin`. N'utilisez **jamais** `--allowerasing` sur un nœud Kubernetes, où `containerd` fait tourner le cluster. L'installation a été validée avec Podman 4.9, la version livrée avec RHEL 9.4.

| Ressource | Minimum | Recommandé |
|---|---|---|
| vCPU | 4 | 8 |
| RAM | 8 Go | 16 Go |
| Disque `/var/lib/containers` | 50 Go | 100 Go (InfluxDB : 90 jours de métriques) |
| Disque `/var/backups/noc` | 20 Go | selon la rétention (14 jours par défaut) |

**Également nécessaires :**
- l'heure synchronisée (`chronyd`), indispensable pour les sessions et le TLS ;
- un enregistrement DNS pour le serveur, par exemple `cex-dashboard.adcm.orangecm` ;
- un **certificat** de l'AC interne pour ce nom (`tls.crt` contenant la chaîne, et `tls.key`).

### Flux réseau à demander

Exécutez `deploy/rhel/check-flows.sh` (voir l'étape 6) pour vérifier chaque flux avant et après l'ouverture.

| Sens | Source | Destination | Port | Usage |
|---|---|---|---|---|
| Entrant | postes des opérateurs, écrans NOC | serveur NOC | **443**/tcp (80/tcp pour la redirection) | interface et API |
| Sortant | serveur NOC | vCenter | 443/tcp | API vSphere, latences (PerformanceManager) |
| Sortant | serveur NOC | Huawei Dorado | 8088/tcp | API REST |
| Sortant | serveur NOC | Dell Unity, PowerStore, Scality | 443/tcp | API REST |
| Sortant | serveur NOC | DataDomain | 3009/tcp | API REST |
| Sortant | serveur NOC | NetBackup | 1556/tcp | API REST |
| Sortant | serveur NOC | Veritas Access | 22/tcp | SSH |
| Sortant | serveur NOC | contrôleurs de domaine | 636/tcp (ou 389 + StartTLS) | connexion AD (si activée) |
| Sortant | serveur NOC | relais SMTP | 25 ou 587/tcp | alertes e-mail |
| Sortant | serveur NOC | DNS, NTP | 53, 123 | résolution, heure |

Le serveur n'a **besoin d'aucun accès à Internet**. Seul le poste de construction en a besoin (étape 2).

## 2. Construire le paquet (poste avec Internet)

Sur un poste Linux équipé de `podman` (ou `docker`), depuis le dépôt :

```bash
git clone https://github.com/dagen2304/dashbord-cex.git
```

```bash
cd dashbord-cex && ./deploy/rhel/build-images.sh
```

La machine de construction doit accéder, directement ou via un proxy, à :
- **Docker Hub** (`registry-1.docker.io`, `auth.docker.io`) ;
- **PyPI** (`pypi.org`, `files.pythonhosted.org`) ;
- **npm** (`registry.npmjs.org`).

Aucun miroir de paquets Linux (Debian, Alpine, RHEL) n'est nécessaire.

Le script **teste d'abord ces accès**, avec 10 secondes maximum par site. S'il échoue, il s'arrête en expliquant la marche à suivre. Avec un proxy d'entreprise :
- exportez `HTTPS_PROXY` (et `NO_PROXY`) avant de lancer le script ; il les transmet aux constructions ;
- avec `docker`, déclarez aussi le proxy au démon Docker. La marche à suivre est affichée par le script.

Le script produit `dist/noc-dashboard-<version>-rhel.tar.gz` et son empreinte `.sha256`. Le paquet contient les images de l'application, celles de PostgreSQL, InfluxDB et Redis en versions figées, les scripts et cette documentation.

La version est lue dans le fichier `VERSION`. Pour une nouvelle livraison, incrémentez ce fichier, ou passez la version en argument : `./deploy/rhel/build-images.sh 3.0.1`.

## 3. Transférer et installer

Copiez le paquet sur le serveur (`scp`, partage, etc.), puis :

```bash
sha256sum -c noc-dashboard-3.0.0-rhel.tar.gz.sha256
```

```bash
tar xzf noc-dashboard-3.0.0-rhel.tar.gz && cd noc-dashboard-3.0.0
```

```bash
sudo ./deploy/rhel/install.sh
```

`install.sh` effectue, dans l'ordre :
1. il vérifie le système, Podman et l'intégrité des images ;
2. il charge les images ;
3. il crée `/etc/noc/noc.env` (configuration) et `/etc/noc/secrets.env` (secrets facultatifs, mode 600) ;
4. il génère les secrets obligatoires dans `podman secret` (base, InfluxDB, `SECRET_KEY`, `JWT_SECRET`, mot de passe admin initial) ;
5. il crée un certificat **auto-signé provisoire** si aucun n'est fourni (étape 4) ;
6. il installe les services, ouvre le pare-feu (443 et 80) et programme la sauvegarde quotidienne de 02 h 30 ;
7. il démarre tout dans l'ordre et vérifie que `https://127.0.0.1/` répond.

**Premier accès.** Le mot de passe administrateur initial est dans `/root/noc-admin-initial-password.txt`. Connectez-vous avec le compte `admin`, changez le mot de passe dans « Mon compte », puis supprimez ce fichier.

Le script est **relançable sans risque** : il conserve la configuration, les secrets, les données et le certificat existants.

## 4. Certificat HTTPS

Déposez le certificat de l'AC interne (chaîne complète, PEM) et sa clé :

```bash
sudo install -m 644 cex-dashboard.crt /etc/noc/tls/tls.crt
```

```bash
sudo install -m 600 cex-dashboard.key /etc/noc/tls/tls.key
```

```bash
sudo /opt/noc/deploy/rhel/install.sh --renew-tls
```

Utilisez la même commande à chaque renouvellement du certificat. La clé est chargée dans le secret `noc_tls_key`, lisible par le seul processus nginx.

## 5. Configuration

`/etc/noc/noc.env` contient la configuration non secrète, et `/etc/noc/secrets.env` les secrets facultatifs. Après une modification :

```bash
sudo systemctl restart noc-backend noc-worker noc-beat
```

**Active Directory.**
- Dans `noc.env` : `LDAP_ENABLED=true`, `LDAP_URL`, `LDAP_BASE_DN`, `LDAP_BIND_DN` et les trois `LDAP_GROUP_*`.
- Le mot de passe du compte de service va dans `secrets.env` (`LDAP_BIND_PASSWORD`).
- Déposez l'AC des contrôleurs de domaine dans `/etc/noc/ca/ldap-ca.pem`, puis renseignez `LDAP_CA_FILE=/certs/ldap-ca.pem`.

**AC interne des équipements.** Déposez le fichier dans `/etc/noc/ca/devices-ca.pem`, puis renseignez `DEVICE_CA_BUNDLE=/certs/devices-ca.pem`.

**SMTP.**
- Serveur, port et destinataires : dans l'interface, Paramètres → Général.
- Identifiants, si le relais en exige : dans `secrets.env`.

## 6. Vérifier les flux

**Avant l'installation**, avec une liste de flux à adapter :

```bash
cp deploy/rhel/flows.example.txt flows.txt && vi flows.txt
```

```bash
./deploy/rhel/check-flows.sh flows.txt
```

**Après l'installation**, une fois les équipements saisis, le script tire la liste de l'inventaire, en y ajoutant l'AD et le SMTP configurés. Il teste depuis le conteneur de collecte, c'est-à-dire le chemin réel :

```bash
sudo /opt/noc/deploy/rhel/check-flows.sh --inventory --from-container
```

Pour chaque flux, le résultat indique la résolution DNS, l'ouverture TCP et, sur les ports TLS, la poignée de main. Ce tableau est à transmettre à l'équipe réseau, et il resservira pour le cluster Kubernetes.

## 7. Exploitation

| Besoin | Commande |
|---|---|
| État des services | `systemctl status 'noc-*'` ou `podman ps` |
| Journaux | `journalctl -u noc-backend -f` (ou `noc-worker`, `noc-frontend`…) |
| Redémarrer l'application | `systemctl restart noc-backend noc-worker noc-beat noc-frontend` |
| Arrêter tout | `systemctl stop noc-frontend noc-beat noc-worker noc-backend noc-redis noc-influxdb noc-postgres` |
| Version en service | `podman image inspect -f '{{index .Labels "noc.version"}}' localhost/noc-backend:current` |
| Sauvegarde immédiate | `systemctl start noc-backup` (résultat : `journalctl -u noc-backup`) |
| Prochaine sauvegarde | `systemctl list-timers noc-backup.timer` |

### Mise à jour

Construisez le nouveau paquet (étape 2), transférez-le, puis :

```bash
tar xzf noc-dashboard-<version>-rhel.tar.gz && sudo ./noc-dashboard-<version>/deploy/rhel/update.sh
```

`update.sh` effectue, dans l'ordre :
1. une **sauvegarde** ;
2. le chargement des nouvelles images (l'actuelle devient `previous`) ;
3. la création des secrets nouvellement requis (ex. mot de passe Redis en 3.2.2) ;
4. le redémarrage, une à une, des **bases dont l'image ou la configuration change** (correctifs de sécurité de PostgreSQL, InfluxDB ou Redis), chacune devant redevenir saine ; les données sont conservées ;
5. le redémarrage de l'application.

Si l'API ou l'interface ne redevient pas saine, le script **revient automatiquement** à la version précédente.

### Retour arrière manuel

```bash
sudo /opt/noc/deploy/rhel/rollback.sh
```

Le retour arrière ne concerne que l'application. Les migrations de la v3 étant additives, la version précédente lit la base sans difficulté. Les bases restent dans leur nouvelle version (correctifs mineurs, compatibles), et le mot de passe Redis est aussi transmis à une version antérieure à 3.2.2 (le secret `noc_redis_url` est prioritaire sur `REDIS_URL`).

### Sauvegarde et restauration

**Contenu de chaque sauvegarde**, dans `/var/backups/noc/<date>/` (dossier en 700) :
- `postgres.dump` (`pg_dump`, format personnalisé) ;
- `influxdb/` (`influx backup`) ;
- `etc-noc.tar.gz` (configuration) ;
- `secrets.tar.gz`.

> ⚠ `secrets.tar.gz` contient `SECRET_KEY`. Sans elle, les mots de passe des équipements restaurés sont illisibles. Copiez les sauvegardes vers un stockage protégé.

**Restaurer** (même serveur ou serveur neuf) :

1. **Sur un serveur neuf uniquement : recréer les secrets avant `install.sh`.** Pour chaque fichier extrait de `secrets.tar.gz` :

   ```bash
   sudo podman secret create noc_xxx /chemin/noc_xxx
   ```

   Pour `noc_tls_key`, restaurez plutôt `/etc/noc/tls/` à partir de `etc-noc.tar.gz`. Lancez ensuite `install.sh`.

2. **Arrêter l'application :**

   ```bash
   sudo systemctl stop noc-frontend noc-beat noc-worker noc-backend
   ```

3. **Restaurer PostgreSQL :**

   ```bash
   sudo podman exec -i noc-postgres pg_restore -U noc_admin -d noc_dashboard --clean --if-exists < postgres.dump
   ```

4. **Copier la sauvegarde InfluxDB dans le conteneur :**

   ```bash
   sudo podman cp influxdb noc-influxdb:/var/lib/influxdb2/restore
   ```

5. **Restaurer InfluxDB :**

   ```bash
   sudo podman exec noc-influxdb sh -c 'influx restore /var/lib/influxdb2/restore --full --token "$DOCKER_INFLUXDB_INIT_ADMIN_TOKEN"'
   ```

6. **Redémarrer l'application :**

   ```bash
   sudo systemctl start noc-backend noc-worker noc-beat noc-frontend
   ```

## 8. Dépannage

| Symptôme | Piste |
|---|---|
| Un service ne démarre pas | `journalctl -u noc-<service> -n 100` ; `podman logs noc-<service>` |
| Refus SELinux | `ausearch -m avc -ts recent`. Les montages sont étiquetés `:z` ; un fichier ajouté à la main dans `/etc/noc` peut nécessiter `restorecon -R /etc/noc` |
| Interface inaccessible | `firewall-cmd --list-services` (https, http) ; `curl -kI https://127.0.0.1/` sur le serveur |
| Équipement « Injoignable » | `check-flows.sh --inventory --from-container`, puis le mode TLS de l'équipement dans l'inventaire (« Lire le certificat ») |
| Connexion AD refusée (503) | flux 636 vers les DC, `LDAP_CA_FILE`, compte de service. Le compte local `admin` reste utilisable |

## 9. Passage ultérieur sur Kubernetes

Rien n'est à refaire côté application : ce sont les **mêmes images** et les **mêmes réglages** (non-root, lecture seule, secrets séparés). Il suffit de :
1. **publier les images** dans le registre du cluster, avec les mêmes versions ;
2. **reprendre les secrets** (`podman secret inspect --showsecret`, ou `secrets.tar.gz`) dans `noc-secrets`, en gardant **la même `SECRET_KEY`** ;
3. **restaurer la dernière sauvegarde** PostgreSQL et InfluxDB dans le cluster ;
4. **reporter dans les `NetworkPolicy`** de `k8s-production.yaml` les flux validés ici avec `check-flows.sh`.
