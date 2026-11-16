# =============================================================================
# Stage 1 — Build : compilation TypeScript + Vite
# =============================================================================
FROM node:20-alpine AS build

WORKDIR /app

# Copier les fichiers de dépendances en premier (cache Docker)
COPY package.json package-lock.json ./

# Installation déterministe (ci = clean install, respecte le lockfile)
RUN npm ci --ignore-scripts

# Copier le code source et builder
COPY . .

# On compile l'application avec un placeholder unique
ENV VITE_API_URL=__VITE_API_URL__
RUN npm run build

# =============================================================================
# Stage 2 — Production : serveur Nginx statique
# =============================================================================
FROM nginx:1.27-alpine AS production

# Supprimer la config par défaut de Nginx
RUN rm /etc/nginx/conf.d/default.conf

# Copier notre configuration Nginx optimisée SPA
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copier uniquement les fichiers buildés depuis le stage précédent
COPY --from=build /app/dist /usr/share/nginx/html

# Ajouter le script d'injection d'environnement au démarrage
COPY inject-env.sh /docker-entrypoint.d/99-inject-env.sh
RUN sed -i 's/\r$//' /docker-entrypoint.d/99-inject-env.sh && \
    chmod +x /docker-entrypoint.d/99-inject-env.sh

EXPOSE 80

# Healthcheck intégré
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://localhost:80/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
