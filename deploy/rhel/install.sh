#!/usr/bin/env bash
# ==============================================================================
# Installation de NOC INFRA sur un serveur RHEL 8.8+ / 9.2+ (Podman + Quadlet).
#
#   tar xzf noc-dashboard-<version>-rhel.tar.gz
#   cd noc-dashboard-<version>
#   sudo ./deploy/rhel/install.sh
#
# Relançable sans risque : ce qui existe déjà (configuration, secrets, données,
# certificat) est conservé. Options :
#   --renew-tls   recharge /etc/noc/tls/tls.crt + tls.key (certificat renouvelé)
# ==============================================================================
set -euo pipefail
KIT=$(cd "$(dirname "$0")/../.." && pwd)
# shellcheck source=deploy/rhel/lib.sh
source "$KIT/deploy/rhel/lib.sh"

RENEW_TLS=false
[[ ${1:-} == --renew-tls ]] && RENEW_TLS=true

require_root
require_podman
VERSION=$(cat "$KIT/VERSION")
info "Installation de NOC INFRA $VERSION depuis $KIT"

# ── 0. Contrôles ──────────────────────────────────────────────────────────────
. /etc/os-release
case "${ID:-}" in
    rhel|rocky|almalinux|centos) ok "Système : $PRETTY_NAME" ;;
    *) warn "Système non RHEL ($PRETTY_NAME) : non testé." ;;
esac
if command -v getenforce >/dev/null; then info "SELinux : $(getenforce) (les volumes sont étiquetés automatiquement)"; fi
if [[ -f $KIT/SHA256SUMS ]]; then
    (cd "$KIT" && sha256sum -c --quiet SHA256SUMS) || die "Empreinte des images incorrecte : paquet corrompu."
    ok "Intégrité du paquet vérifiée."
fi

# ── 1. Images ─────────────────────────────────────────────────────────────────
if [[ -f $KIT/images.tar ]]; then
    info "Chargement des images (hors ligne)…"
    podman load -q -i "$KIT/images.tar" >/dev/null
fi
for img in "${NOC_IMAGES[@]}"; do
    podman image exists "localhost/$img:$VERSION" || die "Image localhost/$img:$VERSION introuvable."
    if podman image exists "localhost/$img:current"; then
        if [[ $(podman image inspect -f '{{.Id}}' "localhost/$img:current") != $(podman image inspect -f '{{.Id}}' "localhost/$img:$VERSION") ]]; then
            podman tag "localhost/$img:current" "localhost/$img:previous"
        fi
    fi
    podman tag "localhost/$img:$VERSION" "localhost/$img:current"
done
ok "Images prêtes (version courante : $VERSION)."

# ── 2. Configuration ──────────────────────────────────────────────────────────
install -d -m 750 "$NOC_ETC" "$NOC_ETC/tls" "$NOC_ETC/ca"
[[ -f $NOC_ETC/noc.env ]] || { install -m 640 "$KIT/deploy/rhel/config/noc.env.template" "$NOC_ETC/noc.env"; ok "Créé : $NOC_ETC/noc.env"; }
[[ -f $NOC_ETC/secrets.env ]] || { install -m 600 "$KIT/deploy/rhel/config/secrets.env.template" "$NOC_ETC/secrets.env"; ok "Créé : $NOC_ETC/secrets.env"; }
chmod 600 "$NOC_ETC/secrets.env"
install -m 644 "$KIT/deploy/rhel/config/nginx-tls.conf" "$NOC_ETC/nginx-tls.conf"

# ── 3. Certificat HTTPS ───────────────────────────────────────────────────────
if [[ ! -f $NOC_ETC/tls/tls.crt || ! -f $NOC_ETC/tls/tls.key ]]; then
    fqdn=$(hostname -f 2>/dev/null || hostname)
    warn "Pas de certificat dans $NOC_ETC/tls : certificat AUTO-SIGNÉ provisoire (30 jours) pour $fqdn."
    warn "Remplacez-le par un certificat de l'AC interne puis : install.sh --renew-tls"
    openssl req -x509 -newkey rsa:3072 -nodes -days 30 -subj "/CN=$fqdn" \
        -addext "subjectAltName=DNS:$fqdn,DNS:$(hostname -s)" \
        -keyout "$NOC_ETC/tls/tls.key" -out "$NOC_ETC/tls/tls.crt" 2>/dev/null
    RENEW_TLS=true
fi
chmod 600 "$NOC_ETC/tls/tls.key"; chmod 644 "$NOC_ETC/tls/tls.crt"
openssl x509 -noout -in "$NOC_ETC/tls/tls.crt" >/dev/null 2>&1 || die "$NOC_ETC/tls/tls.crt n'est pas un certificat PEM."
if $RENEW_TLS || ! secret_exists noc_tls_key; then
    podman secret rm noc_tls_key >/dev/null 2>&1 || true
    podman secret create noc_tls_key "$NOC_ETC/tls/tls.key" >/dev/null
    ok "Clé privée TLS chargée (secret noc_tls_key)."
fi
info "Certificat : $(openssl x509 -noout -subject -enddate -in "$NOC_ETC/tls/tls.crt" | tr '\n' ' ')"

# ── 4. Secrets (générés une seule fois, jamais écrits dans un fichier) ────────
if ! secret_exists noc_postgres_password || ! secret_exists noc_database_url; then
    if secret_exists noc_postgres_password || secret_exists noc_database_url; then
        die "Un seul des secrets noc_postgres_password / noc_database_url existe : situation incohérente, voir la documentation."
    fi
    pg_pass=$(random_secret 32)
    ensure_secret noc_postgres_password "$pg_pass"
    ensure_secret noc_database_url "postgresql://noc_admin:$pg_pass@noc-postgres:5432/noc_dashboard"
    unset pg_pass
fi
ensure_secret noc_influxdb_password "$(random_secret 32)"
ensure_secret noc_influxdb_token "$(random_secret 48)"
ensure_secret noc_secret_key "$(random_secret 48)"
ensure_secret noc_jwt_secret "$(random_secret 48)"
if ! secret_exists noc_admin_initial_password; then
    admin_pass="Noc-$(random_secret 16)-$(random_secret 4)!"
    ensure_secret noc_admin_initial_password "$admin_pass"
    install -m 600 /dev/null /root/noc-admin-initial-password.txt
    printf 'Compte : admin\nMot de passe initial : %s\n(À changer dans « Mon compte » à la première connexion, puis supprimer ce fichier.)\n' \
        "$admin_pass" > /root/noc-admin-initial-password.txt
    unset admin_pass
    ok "Mot de passe administrateur initial : /root/noc-admin-initial-password.txt (root uniquement)."
fi

# ── 5. Volumes de données (propriétaire = utilisateur non-root de chaque image) ──
for spec in noc-postgres:70 noc-influxdb:1000; do
    vol=${spec%%:*}; uid=${spec##*:}
    podman volume create --ignore "$vol" >/dev/null
    chown "$uid:$uid" "$(podman volume inspect -f '{{.Mountpoint}}' "$vol")"
done
ok "Volumes noc-postgres et noc-influxdb prêts."

# ── 6. Unités systemd (Quadlet), outils, sauvegardes ─────────────────────────
install -d /opt/noc
rm -rf /opt/noc/deploy.new && cp -r "$KIT/deploy" /opt/noc/deploy.new && rm -rf /opt/noc/deploy && mv /opt/noc/deploy.new /opt/noc/deploy
cp "$KIT/VERSION" /opt/noc/VERSION
install -m 644 "$KIT"/deploy/rhel/quadlet/* "$NOC_QUADLET_DIR"/
install -m 644 "$KIT"/deploy/rhel/systemd/noc-backup.service "$KIT"/deploy/rhel/systemd/noc-backup.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now noc-backup.timer >/dev/null
ok "Services installés (démarrage automatique au boot) et sauvegarde quotidienne programmée."

# ── 7. Pare-feu ───────────────────────────────────────────────────────────────
if systemctl is-active --quiet firewalld; then
    firewall-cmd --quiet --permanent --add-service=https --add-service=http
    firewall-cmd --quiet --reload
    ok "Pare-feu : HTTPS (443) et HTTP (80, redirigé vers HTTPS) ouverts."
else
    warn "firewalld inactif : vérifiez l'ouverture des ports 443 et 80."
fi

# ── 8. Démarrage dans l'ordre ─────────────────────────────────────────────────
info "Démarrage des bases…"
systemctl start noc-postgres noc-influxdb noc-redis
for c in noc-postgres noc-influxdb noc-redis; do wait_healthy "$c" 240 || die "$c ne démarre pas : journalctl -u $c"; done
info "Démarrage de l'application…"
systemctl restart noc-backend
wait_healthy noc-backend 240 || die "L'API ne démarre pas : journalctl -u noc-backend"
systemctl restart noc-worker noc-beat noc-frontend
wait_healthy noc-frontend 120 || die "L'interface ne démarre pas : journalctl -u noc-frontend"

code=$(curl -ks -o /dev/null -w '%{http_code}' https://127.0.0.1/ || true)
[[ $code == 200 ]] || die "https://127.0.0.1/ répond $code"
ok "NOC INFRA $VERSION opérationnel : https://$(hostname -f 2>/dev/null || hostname)/"
info "Étapes suivantes : docs/DEPLOIEMENT_RHEL.md (certificat, AD, flux, équipements)."
