#!/usr/bin/env bash
# ==============================================================================
# Vérifie les flux réseau dont NOC INFRA a besoin (avant ou après installation).
#
#   ./check-flows.sh flows.txt          flux listés dans un fichier (voir flows.example.txt)
#   ./check-flows.sh --inventory        équipements de l'inventaire + AD + SMTP configurés
#   ./check-flows.sh --inventory --from-container
#                                       teste depuis le conteneur de collecte (chemin réel)
#
# Pour chaque flux : résolution DNS, ouverture TCP (5 s), et pour les ports TLS la
# poignée de main TLS. Code retour 1 si au moins un flux est fermé.
# Sortie au format tableau, à transmettre à l'équipe réseau pour les ouvertures.
# ==============================================================================
set -uo pipefail
# shellcheck source=deploy/rhel/lib.sh
source "$(dirname "$0")/lib.sh"

FROM_CONTAINER=false
INVENTORY=false
FILE=""
for arg in "$@"; do
    case $arg in
        --from-container) FROM_CONTAINER=true ;;
        --inventory) INVENTORY=true ;;
        -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
        *) FILE=$arg ;;
    esac
done
$INVENTORY || [[ -n $FILE ]] || { sed -n '2,14p' "$0"; exit 2; }

flows=()   # "hôte port libellé"
if [[ -n $FILE ]]; then
    while read -r host port label; do
        [[ -z ${host:-} || ${host:0:1} == "#" ]] && continue
        flows+=("$host $port ${label:-}")
    done < "$FILE"
fi
if $INVENTORY; then
    command -v podman >/dev/null || die "--inventory nécessite l'application installée (podman)."
    while IFS='|' read -r host port name type; do
        [[ -n $host ]] && flows+=("$host $port équipement:${type}:${name// /_}")
    done < <(podman exec noc-postgres psql -U noc_admin -d noc_dashboard -At -F'|' -c \
        "SELECT hostname_ip, port, display_name, device_type FROM infrastructure_devices
         WHERE NOT is_archived AND hostname_ip NOT LIKE '%.demo.invalid'")
    smtp=$(podman exec noc-postgres psql -U noc_admin -d noc_dashboard -At -c \
        "SELECT string_agg(value, ' ' ORDER BY key DESC) FROM system_settings WHERE key IN ('smtp_server','smtp_port')")
    [[ -n $smtp && $smtp != "localhost "* ]] && flows+=("${smtp% *} ${smtp#* } smtp")
    # shellcheck disable=SC1091
    ldap_url=$(. /etc/noc/noc.env 2>/dev/null; [[ ${LDAP_ENABLED:-false} == true ]] && echo "${LDAP_URL:-}")
    IFS=',' read -ra urls <<< "$ldap_url"
    for u in "${urls[@]}"; do
        u=${u// /}; [[ -z $u ]] && continue
        hp=${u#*://}; h=${hp%%:*}; p=${hp##*:}; [[ $p == "$hp" ]] && { [[ $u == ldaps* ]] && p=636 || p=389; }
        flows+=("$h $p active-directory")
    done
fi
(( ${#flows[@]} )) || die "Aucun flux à tester."

probe() {
    # probe <hôte> <port> → « dns|tcp|tls » : ok / ko / -
    local h=$1 p=$2
    if $FROM_CONTAINER; then
        podman exec noc-worker python3 - "$h" "$p" <<'PY'
import socket, ssl, sys
h, p = sys.argv[1], int(sys.argv[2])
try: socket.getaddrinfo(h, p); dns = "ok"
except OSError: print("ko ko -"); sys.exit()
try: s = socket.create_connection((h, p), timeout=5)
except OSError: print("ok ko -"); sys.exit()
tls = "-"
if p in (443, 636, 3009, 8088, 1556):
    ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    try: ctx.wrap_socket(s, server_hostname=h).close(); tls = "ok"
    except (ssl.SSLError, OSError): tls = "ko"
print(f"ok ok {tls}")
PY
        return
    fi
    local dns=ok tcp=ko tls=-
    getent hosts "$h" >/dev/null || [[ $h =~ ^[0-9.]+$ ]] || { echo "ko ko -"; return; }
    timeout 5 bash -c ">/dev/tcp/$h/$p" 2>/dev/null && tcp=ok
    if [[ $tcp == ok ]] && [[ $p =~ ^(443|636|3009|8088|1556)$ ]]; then
        # Lecture seule de la poignée de main TLS (aucune donnée envoyée).
        if timeout 8 openssl s_client -connect "$h:$p" -servername "$h" </dev/null >/dev/null 2>&1; then tls=ok; else tls=ko; fi
    fi
    echo "$dns $tcp $tls"
}

printf '%-40s %-6s %-5s %-5s %-5s %s\n' "HÔTE" "PORT" "DNS" "TCP" "TLS" "USAGE"
failures=0
for f in "${flows[@]}"; do
    read -r h p label <<< "$f"
    read -r dns tcp tls <<< "$(probe "$h" "$p")"
    [[ $tcp == ok && $tls != ko ]] || failures=$((failures + 1))
    printf '%-40s %-6s %-5s %-5s %-5s %s\n' "$h" "$p" "$dns" "$tcp" "$tls" "$label"
done
echo
if (( failures )); then
    source_name=$(hostname)
    $FROM_CONTAINER && source_name="conteneur noc-worker"
    warn "$failures flux fermé(s) ou en échec sur ${#flows[@]} (source : $source_name)."
    exit 1
fi
ok "Tous les flux (${#flows[@]}) sont ouverts."
