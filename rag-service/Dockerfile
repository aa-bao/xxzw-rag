# ---- Node build stage ----
FROM node:22-alpine AS node-build
WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# ---- Python runtime stage ----
FROM python:3.12-slim
WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.7.5 /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy

# Install Python deps
COPY rag-service/pyproject.toml rag-service/uv.lock ./
RUN uv sync --frozen --no-dev

# Copy source
COPY rag-service/ ./
COPY --from=node-build /app/web/dist ./static

# Non-root user
RUN useradd --create-home --shell /bin/bash appuser && chown -R appuser:appuser /app
USER appuser

# Entrypoint
COPY rag-service/docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
