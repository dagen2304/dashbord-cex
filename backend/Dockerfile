FROM python:3.10-slim-bullseye

WORKDIR /app

# Installer les dépendances système nécessaires
# --fix-missing : robustesse en cas d'erreur réseau transitoire sur les mirrors Debian
RUN apt-get update && apt-get install -y --no-install-recommends --fix-missing \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copier et installer les dépendances Python (layer cachée si requirements.txt inchangé)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copier le reste du code
COPY . .

# ── Sécurité : utilisateur non-root ──────────────────────────────────────
RUN addgroup --system --gid 1001 appgroup \
    && adduser --system --uid 1001 --ingroup appgroup appuser
USER appuser

EXPOSE 8000

# ── Healthcheck intégré ──────────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# ── Production : workers multiples, pas de hot-reload ────────────────────
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
