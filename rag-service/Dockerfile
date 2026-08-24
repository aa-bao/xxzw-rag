FROM python:3.12-slim
WORKDIR /opt/tyt-rpa/app

# Install uv from PyPI (avoid ghcr.io pull issue on some networks)
# Use Tencent PyPI mirror (Tencent Cloud server) and longer HTTP timeout
ENV PIP_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple
ENV UV_DEFAULT_INDEX=https://mirrors.cloud.tencent.com/pypi/simple
ENV UV_HTTP_TIMEOUT=600
RUN pip install --no-cache-dir uv==0.7.5
ENV UV_LINK_MODE=copy

# Install Python deps
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
ENV PATH="/opt/tyt-rpa/app/.venv/bin:$PATH"

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
