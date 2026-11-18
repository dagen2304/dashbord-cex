#!/usr/bin/env bash
# Revient à la version précédente de l'application (images « previous »).
# Les données ne sont pas touchées. Pour revenir aussi sur les données : restaurer
# la sauvegarde prise par update.sh (voir docs/DEPLOIEMENT_RHEL.md).
set -euo pipefail
# shellcheck source=deploy/rhel/lib.sh
source "$(dirname "$0")/lib.sh"
require_root

for img in "${NOC_IMAGES[@]}"; do
    podman image exists "localhost/$img:previous" || die "Aucune version précédente de $img."
done
info "Retour de la version $(current_version) à la version précédente…"
for img in "${NOC_IMAGES[@]}"; do
    failed_id=$(podman image inspect -f '{{.Id}}' "localhost/$img:current")
    podman tag "localhost/$img:previous" "localhost/$img:current"
    podman tag "$failed_id" "localhost/$img:rolled-back"
done
systemctl restart noc-backend
wait_healthy noc-backend 240 || die "L'API précédente ne démarre pas non plus : journalctl -u noc-backend"
systemctl restart noc-worker noc-beat noc-frontend
wait_healthy noc-frontend 120 || die "L'interface ne démarre pas : journalctl -u noc-frontend"
ok "Version $(current_version) rétablie."
