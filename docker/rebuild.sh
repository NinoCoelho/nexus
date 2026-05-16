#!/usr/bin/env bash
#
# Rebuild and restart the local Nexus Docker instance.
#
# Usage:
#   ./docker/rebuild.sh                # rebuild + restart + tail logs
#   ./docker/rebuild.sh --no-cache     # full rebuild, no layer cache
#   ./docker/rebuild.sh --clean        # wipe the data volume first (destructive!)
#   ./docker/rebuild.sh --skip-logs    # don't tail logs after startup
#

set -euo pipefail

cd "$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"

NO_CACHE=""
CLEAN=""
SKIP_LOGS=""

for arg in "$@"; do
  case "${arg}" in
    --no-cache)  NO_CACHE="--no-cache" ;;
    --clean)     CLEAN=1 ;;
    --skip-logs) SKIP_LOGS=1 ;;
    -h|--help)
      echo "Usage: $(basename "$0") [--no-cache] [--clean] [--skip-logs]"
      exit 0
      ;;
    *)
      echo "Unknown flag: ${arg}" >&2
      exit 1
      ;;
  esac
done

if ! docker info >/dev/null 2>&1; then
  echo "Error: Docker is not running." >&2
  exit 1
fi

if [ -n "${CLEAN}" ]; then
  echo "WARNING: This will delete all persisted data (sessions, vault, skills, config)."
  echo ""
  read -rp "Type 'yes' to confirm: " confirm
  if [ "${confirm}" != "yes" ]; then
    echo "Aborted." >&2
    exit 1
  fi
  echo "==> Stopping container..."
  docker compose down -v 2>/dev/null || true
fi

echo "==> Stopping and removing old container..."
docker compose down 2>/dev/null || true

echo "==> Building image${NO_CACHE:+ (no-cache)}..."
docker compose build ${NO_CACHE}

echo "==> Starting container..."
docker compose up -d

echo "==> Waiting for health check..."
for i in $(seq 1 30); do
  status=$(docker inspect --format='{{.State.Health.Status}}' nexus 2>/dev/null || echo "missing")
  case "${status}" in
    healthy)
      echo "==> Container is healthy."
      break
      ;;
    unhealthy)
      echo "Error: Container is unhealthy. Check logs with: docker compose logs" >&2
      exit 1
      ;;
  esac
  sleep 2
done

HOST_PORT=$(docker compose port nexus 18989 2>/dev/null | cut -d: -f2 || echo "18989")
echo "==> Nexus is live at http://127.0.0.1:${HOST_PORT}"

if [ -z "${SKIP_LOGS}" ]; then
  echo "==> Tailing logs (Ctrl-C to exit)..."
  docker compose logs -f
fi
