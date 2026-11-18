#!/usr/bin/env bash
# ==============================================================================
# Mise à jour de NOC INFRA vers une nouvelle version, avec retour arrière automatique.
#
#   tar xzf noc-dashboard-<nouvelle>-rhel.tar.gz
#   sudo ./noc-dashboard-<nouvelle>/deploy/rhel/update.sh
#
# 1. sauvegarde complète (bases + configuration + secrets) ;
# 2. chargement des nouvelles images, l'actuelle devient « previous » ;
# 3. redémarrage de l'application (les bases ne sont pas arrêtées) ;
# 4. si l'API ou l'interface ne redeviennent pas saines : retour automatique à « previous ».
# Les migrations de schéma de la v3 sont additives : l'ancienne version reste compatible.
# ==============================================================================
set -euo pipefail
KIT=$(cd "$(dirname "$0")/../.." && pwd)
# shellcheck source=deploy/rhel/lib.sh
source "$KIT/deploy/rhel/lib.sh"
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

info "3/4 Mise à jour des unités et des outils…"
install -m 644 "$KIT"/deploy/rhel/quadlet/* "$NOC_QUADLET_DIR"/
install -m 644 "$KIT/deploy/rhel/config/nginx-tls.conf" "$NOC_ETC/nginx-tls.conf"
rm -rf /opt/noc/deploy && cp -r "$KIT/deploy" /opt/noc/deploy && cp "$KIT/VERSION" /opt/noc/VERSION
systemctl daemon-reload

info "4/4 Redémarrage de l'application…"
systemctl restart noc-backend
if wait_healthy noc-backend 240 && systemctl restart noc-worker noc-beat noc-frontend && wait_healthy noc-frontend 120; then
    ok "Version $NEW en service."
else
    warn "La nouvelle version ne démarre pas correctement : retour à ${OLD:-la version précédente}."
    /opt/noc/deploy/rhel/rollback.sh
    die "Mise à jour annulée. Diagnostic : journalctl -u noc-backend -n 200"
fi
