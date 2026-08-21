FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY githarbor ./githarbor
COPY alembic ./alembic
RUN pip wheel --wheel-dir /wheels .

FROM builder AS dev
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install --no-install-recommends -y git git-lfs skopeo \
    && rm -rf /var/lib/apt/lists/*
COPY tests ./tests
RUN pip install --no-cache-dir /wheels/* \
    && pip install --no-cache-dir -e ".[dev]"

FROM dev AS test
RUN pytest -q \
    && ruff check . \
    && ruff format --check . \
    && mypy githarbor

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/home/githarbor/.local/bin:$PATH"

RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install --no-install-recommends -y git git-lfs skopeo ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin githarbor \
    && mkdir -p /app /data \
    && chown -R githarbor:githarbor /app /data

WORKDIR /app
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels
COPY --chown=githarbor:githarbor alembic.ini ./
COPY --chown=githarbor:githarbor alembic ./alembic

LABEL org.opencontainers.image.title="GitHarbor" \
      org.opencontainers.image.description="Your self-hosted safe harbor for Git repositories." \
      org.opencontainers.image.source="https://github.com/xChaooticz/GitHarbor" \
      org.opencontainers.image.licenses="MIT"

USER githarbor
EXPOSE 8000
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"]
CMD ["uvicorn", "githarbor.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
