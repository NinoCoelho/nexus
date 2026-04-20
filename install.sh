#!/usr/bin/env bash
# Nexus one-line installer.
#
#   curl -fsSL https://raw.githubusercontent.com/NinoCoelho/nexus/main/install.sh | bash
#
# Flags (via env):
#   NEXUS_DIR      install location (default: $HOME/nexus)
#   NEXUS_REF      git ref to check out (default: main)
#   NEXUS_NO_UI    set to 1 to skip UI install
#   NEXUS_NO_INIT  set to 1 to skip `nexus config init`

set -euo pipefail

REPO_URL="${NEXUS_REPO:-https://github.com/NinoCoelho/nexus.git}"
INSTALL_DIR="${NEXUS_DIR:-$HOME/nexus}"
REF="${NEXUS_REF:-main}"

c_bold=$(printf '\033[1m'); c_dim=$(printf '\033[2m'); c_green=$(printf '\033[32m')
c_yellow=$(printf '\033[33m'); c_red=$(printf '\033[31m'); c_reset=$(printf '\033[0m')

log()  { printf "%s==>%s %s\n" "$c_bold" "$c_reset" "$*"; }
warn() { printf "%swarn:%s %s\n" "$c_yellow" "$c_reset" "$*" >&2; }
die()  { printf "%serror:%s %s\n" "$c_red" "$c_reset" "$*" >&2; exit 1; }

need() { command -v "$1" >/dev/null 2>&1; }

# ── Prereqs ───────────────────────────────────────────────────────────────────
log "Checking prerequisites"

need git || die "git is required (install from https://git-scm.com/)"

if ! need uv; then
  log "Installing uv (Python package/runner)"
  curl -fsSL https://astral.sh/uv/install.sh | sh
  # uv installs to ~/.local/bin or ~/.cargo/bin depending on platform
  for p in "$HOME/.local/bin" "$HOME/.cargo/bin"; do
    [ -d "$p" ] && export PATH="$p:$PATH"
  done
  need uv || die "uv installed but not on PATH — re-open your shell and retry"
fi

if ! need python3; then
  warn "python3 not found — uv will download a toolchain on first sync"
fi

UI_ENABLED=1
if [ "${NEXUS_NO_UI:-0}" = "1" ]; then
  UI_ENABLED=0
elif ! need node || ! need npm; then
  warn "node/npm not found — skipping UI install (set NEXUS_NO_UI=0 after installing Node 20+ to enable)"
  UI_ENABLED=0
fi

# ── Clone / update ────────────────────────────────────────────────────────────
if [ -d "$INSTALL_DIR/.git" ]; then
  log "Updating existing checkout in $INSTALL_DIR"
  git -C "$INSTALL_DIR" fetch --quiet origin "$REF"
  git -C "$INSTALL_DIR" checkout --quiet "$REF"
  git -C "$INSTALL_DIR" pull --quiet --ff-only origin "$REF"
else
  log "Cloning $REPO_URL into $INSTALL_DIR"
  git clone --quiet --branch "$REF" "$REPO_URL" "$INSTALL_DIR"
fi

# ── Backend ───────────────────────────────────────────────────────────────────
log "Installing backend (agent/)"
( cd "$INSTALL_DIR/agent" && uv sync --quiet )

# ── Frontend ──────────────────────────────────────────────────────────────────
if [ "$UI_ENABLED" = "1" ]; then
  log "Installing frontend (ui/)"
  ( cd "$INSTALL_DIR/ui" && npm install --silent --no-audit --no-fund )
fi

# ── First-run config ──────────────────────────────────────────────────────────
if [ "${NEXUS_NO_INIT:-0}" != "1" ]; then
  if [ ! -f "$HOME/.nexus/config.toml" ]; then
    log "Writing default config to ~/.nexus/config.toml"
    ( cd "$INSTALL_DIR/agent" && uv run nexus config init >/dev/null )
  else
    log "Keeping existing ~/.nexus/config.toml"
  fi
fi

# ── Convenience launcher ──────────────────────────────────────────────────────
LAUNCHER="$HOME/.local/bin/nexus"
mkdir -p "$(dirname "$LAUNCHER")"
cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
cd "$INSTALL_DIR/agent" && exec uv run nexus "\$@"
EOF
chmod +x "$LAUNCHER"

# ── Done ──────────────────────────────────────────────────────────────────────
cat <<EOF

${c_green}${c_bold}✓ Nexus installed${c_reset}

  Location:  ${c_dim}$INSTALL_DIR${c_reset}
  Launcher:  ${c_dim}$LAUNCHER${c_reset}  ${c_dim}(ensure ~/.local/bin is on PATH)${c_reset}

Next steps:
  ${c_bold}export OPENAI_API_KEY=…${c_reset}       # or ANTHROPIC_API_KEY, etc.
  ${c_bold}nexus daemon start${c_reset}            # background server on :18989
  ${c_bold}nexus chat${c_reset}                    # interactive TUI
$( [ "$UI_ENABLED" = "1" ] && printf "  %scd %s/ui && npm run dev%s  # web UI on :1890\n" "$c_bold" "$INSTALL_DIR" "$c_reset" )

Docs: $INSTALL_DIR/README.md
EOF
