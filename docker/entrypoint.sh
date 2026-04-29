#!/usr/bin/env bash
# Nexus container entrypoint.
#
# Layout inside the container:
#
#   client → 0.0.0.0:${NEXUS_PORT}  (socat)
#                 │
#                 ▼
#         127.0.0.1:${NEXUS_INTERNAL_PORT}  (uvicorn → nexus.main:app)
#
# Why the socat hop: Nexus's auth middleware bypasses authentication only when
# the connecting client IP is loopback. Docker's port mapping makes the source
# IP look like the docker-bridge address inside the container, so a direct bind
# to 0.0.0.0 would 401 every request. socat connects to uvicorn from 127.0.0.1,
# so the server sees only loopback clients and the existing model works as-is.

set -euo pipefail

INTERNAL_PORT="${NEXUS_INTERNAL_PORT:-18988}"
EXTERNAL_PORT="${NEXUS_PORT:-18989}"

# First-run config bootstrap. Runs only when the volume is empty so subsequent
# starts honour user edits.
if [ ! -f "${HOME}/.nexus/config.toml" ]; then
  echo "==> Initializing default ${HOME}/.nexus/config.toml"
  nexus config init >/dev/null 2>&1 || true
fi

NEXUS_PID=""
SOCAT_PID=""

cleanup() {
  trap - TERM INT EXIT
  [ -n "${NEXUS_PID}" ] && kill -TERM "${NEXUS_PID}" 2>/dev/null || true
  [ -n "${SOCAT_PID}" ] && kill -TERM "${SOCAT_PID}" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup TERM INT EXIT

# Start the loopback proxy first so the listening socket exists by the time
# Docker starts forwarding traffic.
socat \
  TCP4-LISTEN:"${EXTERNAL_PORT}",fork,reuseaddr,bind=0.0.0.0 \
  TCP4:127.0.0.1:"${INTERNAL_PORT}" &
SOCAT_PID=$!

# Bind uvicorn to loopback only — socat is the public face inside the container.
python -m uvicorn nexus.main:app \
  --host 127.0.0.1 \
  --port "${INTERNAL_PORT}" &
NEXUS_PID=$!

# Block on the Python server. If it exits, tear socat down too.
wait "${NEXUS_PID}"
RC=$?
exit $RC
