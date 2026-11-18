# Image backend sans aucun appel à apt : toutes les dépendances sont des wheels
# précompilés (--only-binary=:all:). La construction n'a besoin que de Docker Hub et
# de PyPI, et échoue immédiatement si un wheel venait à manquer.

# ── Étape 1 : dépendances Python ──
FROM python:3.12-slim-bookworm AS build
ARG PIP_INDEX_URL=https://pypi.org/simple
COPY requirements.txt .
RUN pip install --no-cache-dir --only-binary=:all: --prefix=/install -r requirements.txt

# ── Étape 2 : image d'exécution ──
FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN addgroup --system --gid 1001 appgroup \
    && adduser --system --uid 1001 --ingroup appgroup --no-create-home appuser
COPY --from=build /install /usr/local
WORKDIR /app
COPY --chown=appuser:appgroup app ./app
COPY --chown=appuser:appgroup scripts ./scripts
COPY --chown=appuser:appgroup main.py .
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=4)"]
# --proxy-headers : l'IP client réelle vient de Nginx (journal d'audit, anti-bruteforce)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4", "--proxy-headers", "--forwarded-allow-ips", "*"]
