FROM python:3.12-slim
WORKDIR /opt/tyt-rpa/app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.7.5 /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy

# Install Python deps
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Copy source and entrypoint
COPY . ./
COPY docker/entrypoint.sh /entrypoint.sh

# Non-root user
RUN useradd --create-home --shell /bin/bash appuser \
    && chown -R appuser:appuser /opt/tyt-rpa/app \
    && chmod +x /entrypoint.sh
USER appuser

# Entrypoint
ENTRYPOINT ["/entrypoint.sh"]
