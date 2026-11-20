#!/usr/bin/env bash
# ==============================================================================
# Mise à jour de NOC INFRA vers une nouvelle version, avec retour arrière automatique.
#
#   tar xzf noc-dashboard-<nouvelle>-rhel.tar.gz
#   sudo ./noc-dashboard-<nouvelle>/deploy/rhel/update.sh
#
# 1. sauvegarde complète (bases + configuration + secrets) ;
# 2. chargement des nouvelles images, l'actuelle devient « previous » ;
# 3. redémarrage des bases dont l'image ou la configuration change, puis de l'application ;
# 4. si l'API ou l'interface ne redeviennent pas saines : retour automatique à « previous ».
# Les migrations de schéma de la v3 sont additives : l'ancienne version reste compatible.
# ==============================================================================
set -euo pipefail
KIT=$(cd "$(dirname "$0")/../.." && pwd)
# shellcheck source=deploy/rhel/lib.sh
source "$KIT/deploy/rhel/lib.sh"
enable_error_trap
require_root
require_podman

NEW=$(cat "$KIT/VERSION")
OLD=$(current_version)
info "Mise à jour : ${OLD:-inconnue} → $NEW"
[[ $NEW != "$OLD" ]] || warn "Version identique : réinstallation des mêmes images."
if [[ -f $KIT/SHA256SUMS ]]; then (cd "$KIT" && sha256sum -c --quiet SHA256SUMS) || die "Paquet corrompu."; fi

info "1/4 Sauvegarde préalable…"
/opt/noc/deploy/rhel/backup.sh || die "Sauvegarde en échec : mise à jour annulée."

info "2/4 Chargement des images…"
[[ -f $KIT/images.tar ]] && podman load -q -i "$KIT/images.tar" >/dev/null
for img in "${NOC_IMAGES[@]}"; do
    podman image exists "localhost/$img:$NEW" || die "Image localhost/$img:$NEW absente du paquet."
    podman tag "localhost/$img:current" "localhost/$img:previous"
    podman tag "localhost/$img:$NEW" "localhost/$img:current"
done

info "3/4 Mise à jour des unités, des secrets et des outils…"
BASES=(noc-postgres noc-influxdb noc-redis)
declare -A before
for b in "${BASES[@]}"; do before[$b]=$(unit_digest "$b.container"); done
redis_secrets_new=false
if ensure_redis_secrets; then redis_secrets_new=true; fi
# REDIS_URL sans mot de passe des versions < 3.2.2 : remplacé par le secret noc_redis_url.
if grep -q '^REDIS_URL=' "$NOC_ETC/noc.env"; then
    sed -i 's|^REDIS_URL=|# Remplacé par le secret noc_redis_url (3.2.2) : REDIS_URL=|' "$NOC_ETC/noc.env"
    info "REDIS_URL retiré de $NOC_ETC/noc.env (mot de passe désormais fourni par un secret)."
fi
install -m 644 "$KIT"/deploy/rhel/quadlet/* "$NOC_QUADLET_DIR"/
install -m 644 "$KIT/deploy/rhel/config/nginx-tls.conf" "$NOC_ETC/nginx-tls.conf"
rm -rf /opt/noc/deploy && cp -r "$KIT/deploy" /opt/noc/deploy && cp "$KIT/VERSION" /opt/noc/VERSION
systemctl daemon-reload

# Bases dont l'image ou la configuration a changé (correctif de sécurité, mot de passe Redis) :
# redémarrées une à une, chacune doit redevenir saine avant de continuer. Leurs données sont
# conservées (volumes) et une sauvegarde vient d'être prise.
for b in "${BASES[@]}"; do
    if [[ $(unit_digest "$b.container") != "${before[$b]}" ]] || { [[ $b == noc-redis ]] && $redis_secrets_new; }; then
        info "Redémarrage de $b (nouvelle image ou configuration)…"
        systemctl restart "$b"
        wait_healthy "$b" 240 || die "$b ne redevient pas sain : journalctl -u $b -n 100 (sauvegarde : $NOC_BACKUP_DIR)"
    fi
done

info "4/4 Redémarrage de l'application…"
systemctl restart noc-backend
if wait_healthy noc-backend 240 && systemctl restart noc-worker noc-beat noc-frontend && wait_healthy noc-frontend 120; then
    ok "Version $NEW en service."
else
    warn "La nouvelle version ne démarre pas correctement : retour à ${OLD:-la version précédente}."
    /opt/noc/deploy/rhel/rollback.sh
    die "Mise à jour annulée. Diagnostic : journalctl -u noc-backend -n 200"
fi
