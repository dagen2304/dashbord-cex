#!/bin/sh
# Ce script s'exécute au démarrage du conteneur Nginx pour remplacer
# le placeholder par la vraie variable d'environnement (K8s/Docker).

API_URL=${VITE_API_URL-""}

echo "Injection de VITE_API_URL=$API_URL dans les fichiers JS compilés..."

# On cherche tous les fichiers JavaScript et on remplace le placeholder __VITE_API_URL__
find /usr/share/nginx/html -type f -name "*.js" -exec sed -i "s|__VITE_API_URL__|$API_URL|g" {} +

echo "Injection terminée."
