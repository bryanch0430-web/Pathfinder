# Pathfinder v5 — one container serves the API and the built React UI.
#
#   docker build -t pathfinder:v5 .
#   docker run --rm -p 8100:8100 -v pathfinder-data:/app/data pathfinder:v5                  # defaults
#   docker run --rm -p 8100:8100 --env-file .env -v pathfinder-data:/app/data pathfinder:v5  # your own .env
#
# Real keys are never baked into the image: pass them at run time, or add them in Settings.

# ---- stage 1: build the UI --------------------------------------------------
FROM node:22-slim AS ui
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- stage 2: run the API + UI ---------------------------------------------
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend
WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ backend/
COPY --from=ui /app/frontend/dist frontend/dist

# Default settings: the example file (placeholders only, no real keys). Anything
# passed with --env-file or -e overrides it, because environment variables take
# precedence over the .env file.
COPY .env.example .env

# SQLite database, traces and caches live in /app/data (mount a volume to keep them).
RUN useradd --create-home pathfinder \
    && mkdir -p /app/data \
    && chown -R pathfinder /app/data
USER pathfinder
VOLUME ["/app/data"]

EXPOSE 8100
CMD ["python", "-m", "uvicorn", "app.main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8100"]
