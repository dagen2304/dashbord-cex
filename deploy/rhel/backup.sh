#!/usr/bin/env bash
# ==============================================================================
# Sauvegarde complète : PostgreSQL (pg_dump), InfluxDB (influx backup), configuration
# et secrets. Lancée chaque nuit par noc-backup.timer et avant chaque mise à jour.
#
# ⚠ Les secrets (dont SECRET_KEY, qui chiffre les mots de passe des équipements) sont
#   indispensables pour restaurer. Ils sont CHIFFRÉS (GPG) si une clé publique est déposée
#   dans /etc/noc/backup-public.asc, la clé privée restant hors du serveur :
#   secrets.tar.gz.gpg et etc-noc.tar.gz.gpg (secrets.env, clé TLS). Les bases restent en
#   clair pour une restauration locale rapide : sans SECRET_KEY, les mots de passe des
#   équipements qu'elles contiennent sont illisibles.
#   Répertoire en 700 (root) ; copiez-le vers un stockage protégé.
# Rétention : NOC_BACKUP_RETENTION_DAYS (14 jours par défaut).
# ==============================================================================
set -euo pipefail
# shellcheck source=deploy/rhel/lib.sh
source "$(dirname "$0")/lib.sh"
enable_error_trap
require_root

RETENTION=${NOC_BACKUP_RETENTION_DAYS:-14}
GPG_KEY=${NOC_BACKUP_GPG_KEY:-$NOC_ETC/backup-public.asc}

# Chiffre un fichier pour la clé publique GPG, puis supprime la version en clair.
encrypt() {
    local f=$1 home
    home=$(mktemp -d)   # trousseau temporaire : rien n'est importé dans celui de root
    if gpg --batch --yes --no-tty --quiet --homedir "$home" --trust-model always \
           --recipient-file "$GPG_KEY" --output "$f.gpg" --encrypt "$f"; then
        shred -u "$f" 2>/dev/null || rm -f "$f"
    else
        rm -rf "$home"
        die "Chiffrement GPG de $f impossible (clé $GPG_KEY invalide ?) : sauvegarde incomplète."
    fi
    rm -rf "$home"
}
DEST="$NOC_BACKUP_DIR/$(date +%Y-%m-%d_%H%M%S)"
umask 077
install -d -m 700 "$NOC_BACKUP_DIR" "$DEST"
info "Sauvegarde vers $DEST"

# PostgreSQL : format « custom » (restauration sélective possible avec pg_restore)
podman exec noc-postgres pg_dump -U noc_admin -d noc_dashboard -Fc > "$DEST/postgres.dump"
ok "PostgreSQL : $(du -h "$DEST/postgres.dump" | cut -f1)"

# InfluxDB : sauvegarde native dans le volume, puis copie hors du conteneur
tmp="/var/lib/influxdb2/.backup-$$"
podman exec noc-influxdb sh -c "influx backup '$tmp' --host http://127.0.0.1:8086 --token \"\$DOCKER_INFLUXDB_INIT_ADMIN_TOKEN\" >/dev/null"
podman cp "noc-influxdb:$tmp" "$DEST/influxdb"
podman exec noc-influxdb rm -rf "$tmp"
ok "InfluxDB : $(du -sh "$DEST/influxdb" | cut -f1)"

# Configuration et secrets
tar -C / -czf "$DEST/etc-noc.tar.gz" etc/noc
secrets_dir=$(mktemp -d)
for s in $(podman secret ls --format '{{.Name}}' | grep '^noc_'); do
    if ! podman secret inspect --showsecret --format '{{.SecretData}}' "$s" > "$secrets_dir/$s" 2>/dev/null; then
        warn "Secret $s non exportable (Podman < 4.7) : notez-le par ailleurs."
        rm -f "$secrets_dir/$s"
    fi
done
tar -C "$secrets_dir" -czf "$DEST/secrets.tar.gz" .
rm -rf "$secrets_dir"
if [[ -s $GPG_KEY ]]; then
    encrypt "$DEST/secrets.tar.gz"
    encrypt "$DEST/etc-noc.tar.gz"
    ok "Secrets et configuration chiffrés (GPG, clé $GPG_KEY)."
else
    warn "Secrets sauvegardés EN CLAIR : déposez une clé publique GPG dans $GPG_KEY (docs/DEPLOIEMENT_RHEL.md, §7)."
fi
cat /opt/noc/VERSION > "$DEST/VERSION" 2>/dev/null || true
ok "Configuration et secrets sauvegardés."

find "$NOC_BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -mtime "+$RETENTION" -exec rm -rf {} +
ok "Sauvegarde terminée ($(du -sh "$DEST" | cut -f1)). Rétention : $RETENTION jours."
