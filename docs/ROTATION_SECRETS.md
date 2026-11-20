# Phase 0 — Secrets à changer

*26/10/2026. Ce document ne contient aucun mot de passe : il dit seulement quoi changer, où et dans quel ordre.*

## Pourquoi tout est considéré comme compromis

- Le fichier `backend/update_creds.py` contenait **en clair** les identifiants de 18 équipements. Il a été supprimé, mais il a pu être copié, sauvegardé ou partagé.
- Les secrets de la plateforme qui sont dans `.env` sont **identiques** à ceux qui étaient écrits en dur dans `k8s-production.yaml`.
- La `SECRET_KEY` actuelle est une valeur connue. Or c'est elle qui chiffre les mots de passe des équipements en base : quiconque a une copie de la base PostgreSQL peut donc **déchiffrer tous les mots de passe de l'inventaire**, y compris ceux des équipements absents de la liste ci-dessous.

## 1. Comptes d'équipements (à faire en premier)

Certains mots de passe sont **réutilisés** d'un compte à l'autre. Il faut donner une valeur différente à chaque compte.

| # | Compte | Type | Équipements concernés | Action |
|---|---|---|---|---|
| A | `ZZZZHDNS@adcm.orangecm` | Compte de domaine AD | labocp-vcsa7, vcenter67-mkp, vcenter67-ndg, vcenter80-mkp, vcenter80-ndg, vcentersa-ndg-aog, vcenter67-agence (`.adcm.orangecm`), 172.26.34.183, 172.26.34.86, 172.22.75.207 | Changer le mot de passe dans l'AD. Vérifier les droits : le compte ne doit avoir que le rôle **lecture seule** dans vCenter |
| B | `ZZZZHDNS` (compte local, même mot de passe que A) | Compte local | 172.26.33.114, 172.23.49.12, 172.21.75.18 | Changer sur chaque équipement, avec une valeur différente de A |
| C | `ZZZZHDNS` (compte local, 2e mot de passe) | Compte local | dorado.makepe.storage.adcm.orangecm, dorado.makepa.storage.adom.orangecm, 172.23.49.213 | Changer sur chaque baie. Rôle monitoring / lecture seule |
| D | `admin` | Administrateur local | 172.26.33.135 | Changer le mot de passe, **et** créer un compte dédié en lecture seule pour le dashboard. Ne plus utiliser `admin` |
| E | `JPMJ3097@localhost` | Compte nominatif | 172.26.34.108 | Changer le mot de passe. Remplacer par un compte de service dédié : un compte personnel ne doit pas servir à la collecte |
| F | Tous les autres équipements enregistrés dans l'inventaire | Divers | Liste obtenue avec la requête ci-dessous | Changer aussi, car leurs mots de passe sont déchiffrables avec la clé connue |

Pour obtenir la liste exhaustive de l'inventaire (ligne F) :

```bash
docker exec -it noc_postgres psql -U noc_admin -d noc_dashboard -c "SELECT display_name, hostname_ip, device_type, username FROM infrastructure_devices ORDER BY device_type, display_name;"
```

- [ ] A — compte de domaine
- [ ] B — comptes locaux (3 équipements)
- [ ] C — comptes Dorado (3 équipements)
- [ ] D — admin + compte lecture seule (1 équipement)
- [ ] E — compte nominatif remplacé (1 équipement)
- [ ] F — reste de l'inventaire

Une fois les comptes changés, saisissez les nouveaux mots de passe dans l'écran **Inventaire** du dashboard, après l'étape 2.

## 2. Secrets de la plateforme

Générez chaque nouvelle valeur avec :

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Suivez cet ordre : le backend refuse désormais de démarrer tant que `SECRET_KEY` a sa valeur actuelle.

1. **`SECRET_KEY`** : les mots de passe en base doivent être re-chiffrés avec la nouvelle clé. Mettez la nouvelle valeur dans `.env`, puis lancez une seule fois :

   ```bash
   docker compose run --rm -e OLD_SECRET_KEY='<ancienne valeur>' backend python -m scripts.rotate_secret_key
   ```

   Le script affiche les équipements qu'il n'a pas pu re-chiffrer : leur mot de passe sera à ressaisir dans l'interface. Les mots de passe de la section 1 seront de toute façon ressaisis.

2. **`POSTGRES_PASSWORD` et `DATABASE_URL`** : `POSTGRES_PASSWORD` ne sert qu'à la première initialisation. Il faut donc aussi changer le mot de passe dans la base :

   ```bash
   docker exec -it noc_postgres psql -U noc_admin -d noc_dashboard -c "\password noc_admin"
   ```

   Reportez ensuite la nouvelle valeur dans `POSTGRES_PASSWORD` **et** dans `DATABASE_URL`.

3. **`INFLUXDB_TOKEN` et `DOCKER_INFLUXDB_INIT_ADMIN_TOKEN`** : créez un nouveau jeton dans InfluxDB (interface ou `influx auth create`) avec un accès en lecture et écriture au seul bucket `noc_metrics`. Supprimez ensuite l'ancien jeton et mettez à jour `INFLUXDB_TOKEN`.

4. **`DOCKER_INFLUXDB_INIT_PASSWORD`** : changez le mot de passe de l'utilisateur admin InfluxDB (`influx user password`).

5. **`JWT_SECRET`** (nouveau en v3) : signe les sessions des utilisateurs. Générez-le comme les autres ; il doit être différent de `SECRET_KEY`. Le changer déconnecte tous les utilisateurs, sans autre conséquence. `INTERNAL_API_KEY` n’est plus utilisée : supprimez-la.

6. Redémarrez la plateforme :

   ```bash
   docker compose up -d --force-recreate
   ```

7. Sous Kubernetes, recréez le Secret `noc-secrets` hors dépôt, avec la commande indiquée en tête de `k8s-production.yaml`.

- [ ] `SECRET_KEY` changée + script de re-chiffrement passé
- [ ] Mot de passe PostgreSQL changé (base + `.env`)
- [ ] Jeton InfluxDB recréé, ancien supprimé
- [ ] Mot de passe admin InfluxDB changé
- [ ] `JWT_SECRET` créé (différent de `SECRET_KEY`), `INTERNAL_API_KEY` supprimée
- [ ] Plateforme redémarrée, collecte vérifiée (`docker compose logs -f celery_worker`)

## 3. Après la rotation

- [ ] Renseigner le destinataire des alertes (`alert_email_to`) dans **Réglages** : il n'y a plus d'adresse par défaut.
- [ ] Copies possibles de `update_creds.py` : sauvegardes, clés USB, messageries, ancien dépôt git, s'il en existe un. Les rechercher et les supprimer, puis lancer `gitleaks` sur tout dépôt existant.
- [ ] Limiter l'accès réseau aux ports 8000 et 80 au seul VLAN NOC, en attendant l'authentification de la phase 1.
