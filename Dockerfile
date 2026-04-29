# syntax=docker/dockerfile:1.7
#
# Nexus container image — backend + bundled UI in a single image.
#
# Auth model: Nexus's middleware grants automatic bypass to loopback clients
# (127.0.0.1 / ::1) and gates everything else behind a tunnel cookie. Inside a
# container that means a bare bind to 0.0.0.0 would 401 every request reaching
# the published port (the docker-bridge source IP isn't loopback). The runtime
# stage keeps uvicorn on 127.0.0.1 and uses a tiny socat proxy to forward the
# externally-exposed port to it — every request the Python server sees comes
# from 127.0.0.1, so the existing security model works unchanged.

# ─── Stage 1: build the React UI ──────────────────────────────────────────────
FROM node:20-alpine AS ui-builder
WORKDIR /build/ui

COPY ui/package.json ui/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY ui/ ./
RUN npm run build

# ─── Stage 2: install Python deps with uv ────────────────────────────────────
FROM python:3.12-slim-bookworm AS backend-builder

# uv is shipped as a static binary; pulling it from the official image avoids
# bootstrapping it via curl.
COPY --from=ghcr.io/astral-sh/uv:0.5.4 /uv /usr/local/bin/uv

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONDONTWRITEBYTECODE=1

# Build tooling needed for native wheels (fastembed, cryptography fallbacks)
# and `git` for the `loom-framework` / `ddgs` git sources in pyproject.
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential git \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app/agent

# Install dependencies first for cache reuse — only re-runs when pyproject changes.
COPY agent/pyproject.toml ./pyproject.toml
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-install-project --no-dev

# Copy the rest of the project and finalize the venv.
COPY agent/ ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev

# ─── Stage 3: runtime ────────────────────────────────────────────────────────
FROM python:3.12-slim-bookworm AS runtime

# socat = loopback proxy (see header). dumb-init = PID 1 zombie reaper +
# signal forwarder so SIGTERM cleanly stops both children.
RUN apt-get update \
 && apt-get install -y --no-install-recommends socat dumb-init ca-certificates \
 && rm -rf /var/lib/apt/lists/*

ARG NEXUS_UID=1000
ARG NEXUS_GID=1000
RUN groupadd --gid ${NEXUS_GID} nexus \
 && useradd --uid ${NEXUS_UID} --gid ${NEXUS_GID} --create-home --shell /bin/bash nexus

WORKDIR /app

# Built UI (served bundled by the backend on a single port).
COPY --from=ui-builder --chown=nexus:nexus /build/ui/dist /app/ui/dist

# Backend source + populated virtualenv.
COPY --from=backend-builder --chown=nexus:nexus /app/agent /app/agent

# Bundled skills — seeded into ~/.nexus/skills on first boot.
COPY --chown=nexus:nexus skills /app/skills

# Entrypoint launcher.
COPY --chown=nexus:nexus docker/entrypoint.sh /usr/local/bin/nexus-entrypoint
RUN chmod +x /usr/local/bin/nexus-entrypoint

# Persist the entire data dir on a named volume.
RUN mkdir -p /home/nexus/.nexus && chown -R nexus:nexus /home/nexus/.nexus
VOLUME ["/home/nexus/.nexus"]

ENV NEXUS_UI_DIST=/app/ui/dist \
    NEXUS_BUILTIN_SKILLS_DIR=/app/skills \
    NEXUS_INTERNAL_PORT=18988 \
    NEXUS_PORT=18989 \
    HOME=/home/nexus \
    PATH="/app/agent/.venv/bin:${PATH}"

USER nexus
WORKDIR /app/agent

EXPOSE 18989

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:18989/health',timeout=3).status==200 else 1)" \
    || exit 1

ENTRYPOINT ["dumb-init", "--", "/usr/local/bin/nexus-entrypoint"]
