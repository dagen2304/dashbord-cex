#!/usr/bin/env bash
# ==============================================================================
# Construit le paquet de livraison pour le serveur RHEL (hors ligne possible).
#
# À lancer sur un poste ou une machine de build AVEC accès à Internet (podman ou
# docker), depuis la racine du dépôt :
#     ./deploy/rhel/build-images.sh            # version lue dans le fichier VERSION
#     ./deploy/rhel/build-images.sh 3.0.1
#
# Produit dist/noc-dashboard-<version>-rhel.tar.gz contenant :
#   - images.tar  : images de l'application + PostgreSQL, InfluxDB, Redis (versions figées)
#   - deploy/rhel : scripts, unités Quadlet, configuration
#   - SHA256SUMS
# ==============================================================================
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
VERSION=${1:-$(cat "$ROOT/VERSION")}
ENGINE=${ENGINE:-$(command -v podman >/dev/null && echo podman || echo docker)}
THIRD_PARTY=(
    docker.io/library/postgres:15.10-alpine
    docker.io/library/influxdb:2.7.11-alpine
    docker.io/library/redis:7.4.1-alpine
)
OUT="$ROOT/dist"
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT

[[ $VERSION =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9]+)?$ ]] || { echo "Version invalide : $VERSION" >&2; exit 1; }
echo "[NOC] Construction de la version $VERSION avec $ENGINE"

"$ENGINE" build --pull -t "localhost/noc-backend:$VERSION" --label "noc.version=$VERSION" "$ROOT/backend"
"$ENGINE" build --pull -t "localhost/noc-frontend:$VERSION" --label "noc.version=$VERSION" "$ROOT/frontend"
for img in "${THIRD_PARTY[@]}"; do "$ENGINE" pull "$img"; done

mkdir -p "$STAGE/noc-dashboard-$VERSION/deploy" "$OUT"
PKG="$STAGE/noc-dashboard-$VERSION"
echo "[NOC] Export des images…"
if [[ $ENGINE == podman ]]; then
    podman save -m --format docker-archive -o "$PKG/images.tar" \
        "localhost/noc-backend:$VERSION" "localhost/noc-frontend:$VERSION" "${THIRD_PARTY[@]}"
else
    docker save -o "$PKG/images.tar" \
        "localhost/noc-backend:$VERSION" "localhost/noc-frontend:$VERSION" "${THIRD_PARTY[@]}"
fi
cp -r "$ROOT/deploy/rhel" "$PKG/deploy/"
echo "$VERSION" > "$PKG/VERSION"
cp "$ROOT/docs/DEPLOIEMENT_RHEL.md" "$PKG/"
(cd "$PKG" && sha256sum images.tar VERSION > SHA256SUMS)

tar -C "$STAGE" -czf "$OUT/noc-dashboard-$VERSION-rhel.tar.gz" "noc-dashboard-$VERSION"
(cd "$OUT" && sha256sum "noc-dashboard-$VERSION-rhel.tar.gz" > "noc-dashboard-$VERSION-rhel.tar.gz.sha256")
echo "[NOC] Paquet prêt : $OUT/noc-dashboard-$VERSION-rhel.tar.gz"
echo "[NOC] Empreinte    : $(cut -d' ' -f1 "$OUT/noc-dashboard-$VERSION-rhel.tar.gz.sha256")"
