#!/usr/bin/env bash
# Fonctions communes aux scripts de déploiement RHEL (à « sourcer »).
# shellcheck disable=SC2034  # variables utilisées par les scripts qui sourcent ce fichier

NOC_ETC=/etc/noc
NOC_QUADLET_DIR=/etc/containers/systemd
NOC_BACKUP_DIR=${NOC_BACKUP_DIR:-/var/backups/noc}
NOC_IMAGES=(noc-backend noc-frontend)

info()  { printf '\033[1;34m[NOC]\033[0m %s\n' "$*"; }
ok()    { printf '\033[1;32m[ OK]\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m[ATT]\033[0m %s\n' "$*" >&2; }
die()   { printf '\033[1;31m[ERR]\033[0m %s\n' "$*" >&2; exit 1; }

require_root() {
    [[ $EUID -eq 0 ]] || die "À lancer en root (sudo)."
}

require_podman() {
    command -v podman >/dev/null || die "Podman absent : dnf install -y podman"
    local v major minor
    v=$(podman version --format '{{.Client.Version}}' 2>/dev/null) || die "Podman ne répond pas."
    major=${v%%.*}; minor=${v#*.}; minor=${minor%%.*}
    if (( major < 4 || (major == 4 && minor < 4) )); then
        die "Podman $v trop ancien : 4.4 minimum (RHEL 8.8+ / 9.2+) pour Quadlet."
    fi
    [[ -x /usr/libexec/podman/quadlet ]] || die "Générateur Quadlet absent (paquet podman trop ancien)."
}

# Secret aléatoire alphanumérique (sans caractère spécial : utilisable dans une URL).
random_secret() {
    # Sans « tr </dev/urandom | head » : head ferme le tube, tr reçoit SIGPIPE (code 141)
    # et, avec « set -o pipefail », le script appelant s'arrêtait sans message.
    local len=${1:-48} out=""
    while (( ${#out} < len )); do
        out+=$(head -c 512 /dev/urandom | LC_ALL=C tr -dc 'A-Za-z0-9')
    done
    printf '%s' "${out:0:len}"
}

secret_exists() { podman secret inspect "$1" >/dev/null 2>&1; }

# Crée un podman secret depuis l'entrée standard s'il n'existe pas encore.
ensure_secret() {
    local name=$1 value=$2
    if secret_exists "$name"; then
        info "Secret $name déjà présent (conservé)."
    else
        printf '%s' "$value" | podman secret create "$name" - >/dev/null
        ok "Secret $name créé."
    fi
}

wait_healthy() {
    # wait_healthy <conteneur> [délai en secondes]
    local name=$1 timeout=${2:-180} waited=0 state
    while (( waited < timeout )); do
        state=$(podman inspect -f '{{.State.Health.Status}}' "$name" 2>/dev/null || echo absent)
        [[ $state == healthy ]] && return 0
        sleep 5; waited=$((waited + 5))
    done
    warn "$name : état « $state » après ${timeout}s."
    return 1
}

current_version() {
    podman image inspect -f '{{index .Labels "noc.version"}}' localhost/noc-backend:current 2>/dev/null || true
}

# Arrêt sur erreur toujours expliqué (sinon « set -e » quitte sans aucun message).
on_error() {
    local code=$? line=${BASH_LINENO[0]} cmd=$BASH_COMMAND
    printf '\033[1;31m[ERR]\033[0m Arrêt inattendu (code %s) à la ligne %s de %s : %s\n' \
        "$code" "$line" "$(basename "${BASH_SOURCE[1]:-$0}")" "$cmd" >&2
    exit "$code"
}
enable_error_trap() { set -E; trap on_error ERR; }
