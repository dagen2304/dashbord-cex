#!/usr/bin/env bash
# ==============================================================================
# Sauvegarde complète : PostgreSQL (pg_dump), InfluxDB (influx backup), configuration
# et secrets. Lancée chaque nuit par noc-backup.timer et avant chaque mise à jour.
#
# ⚠ Les secrets (dont SECRET_KEY, qui chiffre les mots de passe des équipements) sont
#   indispensables pour restaurer : la sauvegarde est donc aussi sensible que le serveur.
#   Répertoire en 700 (root) ; copiez-le vers un stockage protégé.
# Rétention : NOC_BACKUP_RETENTION_DAYS (14 jours par défaut).
# ==============================================================================
set -euo pipefail
# shellcheck source=deploy/rhel/lib.sh
source "$(dirname "$0")/lib.sh"
enable_error_trap
require_root

RETENTION=${NOC_BACKUP_RETENTION_DAYS:-14}
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
cat /opt/noc/VERSION > "$DEST/VERSION" 2>/dev/null || true
ok "Configuration et secrets sauvegardés."

find "$NOC_BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -mtime "+$RETENTION" -exec rm -rf {} +
ok "Sauvegarde terminée ($(du -sh "$DEST" | cut -f1)). Rétention : $RETENTION jours."
