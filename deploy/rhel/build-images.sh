#!/usr/bin/env bash
# ==============================================================================
# Construit le paquet de livraison pour le serveur RHEL (hors ligne possible).
#
# À lancer sur une machine qui accède à Docker Hub, PyPI et npm (directement ou via
# un proxy), avec podman ou docker, depuis la racine du dépôt :
#     ./deploy/rhel/build-images.sh            # version lue dans le fichier VERSION
#     ./deploy/rhel/build-images.sh 3.0.1
#
# Produit dist/noc-dashboard-<version>-rhel.tar.gz contenant :
#   - images.tar  : images de l'application + PostgreSQL, InfluxDB, Redis (versions figées)
#   - deploy/rhel : scripts, unités Quadlet, configuration
#   - SHA256SUMS
#
# Variables utiles :
#   ENGINE=podman|docker     moteur à utiliser (par défaut : podman s'il est installé)
#   SKIP_NETWORK_CHECK=1     ne pas tester l'accès à Docker Hub / PyPI / npm
#   HTTPS_PROXY / NO_PROXY   proxy d'entreprise (transmis aussi aux constructions)
# ==============================================================================
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
VERSION=${1:-$(cat "$ROOT/VERSION")}
ENGINE=${ENGINE:-$(command -v podman >/dev/null && echo podman || echo docker)}
THIRD_PARTY=(
    docker.io/library/postgres:15.20-alpine
    docker.io/library/influxdb:2.7.12-alpine
    docker.io/library/redis:7.4.12-alpine
)
OUT="$ROOT/dist"
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
T0=$(date +%s)

step() { printf '\n\033[1;34m[NOC %s] %s\033[0m\n' "$(date +%H:%M:%S)" "$*"; }
okm()  { printf '\033[1;32m[ OK]\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m[ERR]\033[0m %s\n' "$*" >&2; exit 1; }

[[ $VERSION =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9]+)?$ ]] || fail "Version invalide : $VERSION"
command -v "$ENGINE" >/dev/null || fail "Moteur « $ENGINE » introuvable (installez podman : dnf install -y podman)."
step "Construction de NOC INFRA $VERSION avec $ENGINE ($("$ENGINE" --version 2>/dev/null | head -1))"
# Un démon docker/containerd bloqué fait attendre « docker build » indéfiniment, sans message.
if ! timeout 20 "$ENGINE" info >/dev/null 2>&1; then
    fail "Le moteur « $ENGINE » ne répond pas (vérifiez : systemctl status docker containerd ; journalctl -u containerd -n 30)."
fi
[[ -d $ROOT/deploy/rhel && -f $ROOT/backend/Dockerfile ]] || fail "Lancez le script depuis la branche de la refonte (dossier deploy/rhel absent)."

# Arguments de proxy transmis aux constructions (pip et npm s'exécutent DANS la construction).
BUILD_ARGS=()
for v in HTTP_PROXY HTTPS_PROXY NO_PROXY http_proxy https_proxy no_proxy; do
    if [[ -n ${!v:-} ]]; then BUILD_ARGS+=(--build-arg "$v=${!v}"); fi
done
if [[ ${#BUILD_ARGS[@]} -gt 0 ]]; then
    okm "Proxy détecté, transmis aux constructions : ${HTTPS_PROXY:-${https_proxy:-}}"
fi
# Sortie détaillée de la construction (option propre à docker buildx).
PROGRESS=()
if [[ $ENGINE == docker ]]; then PROGRESS=(--progress=plain); fi
# Podman construit en OCI par défaut, format qui ignore HEALTHCHECK : on force le format docker.
if [[ $ENGINE == podman ]]; then PROGRESS=(--format docker); fi

# ── 1. Accès réseau (évite une attente silencieuse de plusieurs minutes) ─────
if [[ ${SKIP_NETWORK_CHECK:-0} != 1 ]]; then
    step "1/5 Vérification des accès réseau (10 s max par site)"
    bad=0
    # Docker Hub (images de base), PyPI (dépendances Python), npm (interface). Aucun miroir Debian/Alpine.
    for url in https://registry-1.docker.io/v2/ https://auth.docker.io/token https://pypi.org/simple/pip/ \
               https://files.pythonhosted.org/ https://registry.npmjs.org/react; do
        code=$(curl -sS -o /dev/null -m 10 -w '%{http_code}' "$url" 2>/dev/null || true)
        if [[ $code =~ ^(200|301|302|400|401|403|404)$ ]]; then okm "$url (HTTP $code)"
        else printf '\033[1;31m[KO ]\033[0m %s (réponse : %s)\n' "$url" "${code:-aucune}"; bad=1; fi
    done
    if (( bad )); then
        cat >&2 <<'EOF'

Ce serveur n'accède pas à Internet (ou pas sans proxy). Deux solutions :
  a) Proxy d'entreprise : exportez-le puis relancez, et déclarez-le aussi au moteur :
       export HTTPS_PROXY=http://proxy.exemple:8080 NO_PROXY=localhost,127.0.0.1,.adcm.orangecm
       podman : rien de plus (il lit HTTPS_PROXY)
       docker : /etc/systemd/system/docker.service.d/http-proxy.conf
                [Service]
                Environment="HTTPS_PROXY=http://proxy.exemple:8080" "NO_PROXY=localhost,127.0.0.1"
                puis : systemctl daemon-reload && systemctl restart docker
  b) Construction ailleurs : lancez ce script sur un poste qui a Internet, puis copiez
     dist/noc-dashboard-<version>-rhel.tar.gz sur ce serveur (docs/DEPLOIEMENT_RHEL.md, §2).
Pour ignorer ce test : SKIP_NETWORK_CHECK=1 ./deploy/rhel/build-images.sh
EOF
        exit 1
    fi
fi

# ── 2. Images de l'application ────────────────────────────────────────────────
step "2/5 Construction de l'image backend (wheels Python précompilés : quelques minutes)"
"$ENGINE" build --pull "${BUILD_ARGS[@]}" "${PROGRESS[@]}" -t "localhost/noc-backend:$VERSION" \
    --label "noc.version=$VERSION" "$ROOT/backend" 2>&1 | sed -u 's/^/    /'
okm "localhost/noc-backend:$VERSION"

step "3/5 Construction de l'image frontend (npm + Vite : quelques minutes)"
"$ENGINE" build --pull "${BUILD_ARGS[@]}" "${PROGRESS[@]}" -t "localhost/noc-frontend:$VERSION" \
    --label "noc.version=$VERSION" "$ROOT/frontend" 2>&1 | sed -u 's/^/    /'
okm "localhost/noc-frontend:$VERSION"

# ── 3. Images tierces (versions figées) ───────────────────────────────────────
step "4/5 Téléchargement de PostgreSQL, InfluxDB et Redis"
for img in "${THIRD_PARTY[@]}"; do
    "$ENGINE" pull -q "$img" >/dev/null
    okm "$img"
done

# ── 4. Paquet ─────────────────────────────────────────────────────────────────
step "5/5 Création du paquet (export des images : 1 à 2 Go)"
mkdir -p "$STAGE/noc-dashboard-$VERSION/deploy" "$OUT"
PKG="$STAGE/noc-dashboard-$VERSION"
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

okm "Paquet prêt en $(( ($(date +%s) - T0) / 60 )) min : $OUT/noc-dashboard-$VERSION-rhel.tar.gz ($(du -h "$OUT/noc-dashboard-$VERSION-rhel.tar.gz" | cut -f1))"
echo "     Empreinte : $(cut -d' ' -f1 "$OUT/noc-dashboard-$VERSION-rhel.tar.gz.sha256")"
echo "     Installation : tar xzf … && cd noc-dashboard-$VERSION && sudo ./deploy/rhel/install.sh"
