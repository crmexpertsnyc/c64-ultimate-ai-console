# syntax=docker/dockerfile:1
# ---------- frontend build ----------
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------- runtime ----------
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    DATA_DIR=/data FRONTEND_DIST=/app/frontend/dist WEB_PORT=8064
WORKDIR /app/backend
COPY backend/pyproject.toml ./
COPY backend/app ./app
RUN pip install --no-cache-dir ".[mcp]"
COPY --from=web /web/dist /app/frontend/dist
RUN useradd --create-home --uid 1000 c64 && mkdir -p /data && chown c64:c64 /data
USER c64
VOLUME ["/data"]
# 8064 = web UI + API, 11000/11001 UDP = Ultimate video/audio streams
EXPOSE 8064 11000/udp 11001/udp
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import os,urllib.request;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"WEB_PORT\",\"8064\")}/api/health',timeout=4)" || exit 1
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${WEB_PORT}"]
