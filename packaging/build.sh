#!/usr/bin/env bash
#
# Build a standalone Nexus.app bundle (arm64) that ships its own Python
# interpreter, all dependencies, the built UI, and pre-downloaded embedding
# models. See packaging/bootstrap.py for the runtime launcher.
#
# Usage:
#   packaging/build.sh                          # full build (signs ad-hoc)
#   packaging/build.sh --skip-models            # don't pre-download embedding models
#   packaging/build.sh --skip-sign              # skip codesign step
#   packaging/build.sh --identity "Developer ID Application: Name (TEAMID)"
#                                               # sign with a Developer ID certificate
#   packaging/build.sh --identity "Developer ID Application: Name (TEAMID)" \
#                       --notarize \
#                       --notary-apple-id you@email.com \
#                       --notary-team-id TEAMID \
#                       --notary-password app-specific-password
#                                               # sign + notarize + staple
#   packaging/build.sh --bundle-llm none        # no local LLM (default)
#   packaging/build.sh --bundle-llm qwen-3b     # opt-in: Qwen2.5-3B-Instruct (~1.9 GB)
#   packaging/build.sh --bundle-llm gemma-e4b   # opt-in: Gemma 3n E4B (~2.5 GB,
#                                                 emits Gemma's tool_code blocks
#                                                 instead of OpenAI tool_calls
#                                                 — Nexus's loop won't see them)
#
# Signing / notarization can also be set in packaging/build.conf (see
# build.conf.example) or via env vars. Precedence: CLI flags > env vars >
# build.conf.
#
# Optional: ship a pre-configured remote model so a fresh install starts
# with chat working out of the box. All three values are required together;
# pass via flags or DEMO_LLM_BASE_URL / DEMO_LLM_API_KEY / DEMO_LLM_MODEL
# env vars (env vars are preferred so the key never lands in shell history).
# bootstrap.py only seeds the model when ~/.nexus/config.toml is absent
# (true fresh install), so existing users are never overwritten.
#
#   packaging/build.sh \
#       --demo-url   https://llm.knowspace.app/v1 \
#       --demo-key   sk-... \
#       --demo-model nexus
#
set -euo pipefail

cd "$(dirname "$0")/.."
REPO_ROOT="$PWD"
PACKAGING="$REPO_ROOT/packaging"
LOOM_DIR="${LOOM_DIR:-$REPO_ROOT/../loom}"
DIST="$REPO_ROOT/dist"
STAGE="$DIST/stage"
APP_NAME="Nexus"
APP="$DIST/$APP_NAME.app"

PY_VERSION="3.12.7"
PY_BUILD="20241016"
PY_TRIPLE="aarch64-apple-darwin"
PY_DIST="cpython-${PY_VERSION}+${PY_BUILD}-${PY_TRIPLE}-install_only.tar.gz"
PY_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PY_BUILD}/${PY_DIST}"

SKIP_MODELS=0
SKIP_SIGN=0
BUNDLE_LLM="none"
SIGN_IDENTITY="${NEXUS_SIGN_IDENTITY:-}"
NOTARIZE=0
NOTARY_APPLE_ID="${NOTARY_APPLE_ID:-}"
NOTARY_TEAM_ID="${NOTARY_TEAM_ID:-}"
NOTARY_PASSWORD="${NOTARY_PASSWORD:-}"
# Demo-model flags fall back to env vars so the key can stay out of shell history.
DEMO_URL="${DEMO_LLM_BASE_URL:-}"
DEMO_KEY="${DEMO_LLM_API_KEY:-}"
DEMO_MODEL="${DEMO_LLM_MODEL:-}"

# Load optional config file (git-ignored, secrets live here).
# CLI flags and env vars override anything in the file.
BUILD_CONF="$PACKAGING/build.conf"
if [[ -f "$BUILD_CONF" ]]; then
  # shellcheck source=packaging/build.conf
  source "$BUILD_CONF"
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-models) SKIP_MODELS=1; shift ;;
    --skip-sign)   SKIP_SIGN=1; shift ;;
    --bundle-llm)  BUNDLE_LLM="${2:-}"; shift 2 ;;
    --demo-url)    DEMO_URL="${2:-}"; shift 2 ;;
    --demo-key)    DEMO_KEY="${2:-}"; shift 2 ;;
    --demo-model)  DEMO_MODEL="${2:-}"; shift 2 ;;
    --identity)    SIGN_IDENTITY="${2:-}"; shift 2 ;;
    --notarize)    NOTARIZE=1; shift ;;
    --notary-apple-id)  NOTARY_APPLE_ID="${2:-}"; shift 2 ;;
    --notary-team-id)   NOTARY_TEAM_ID="${2:-}"; shift 2 ;;
    --notary-password)  NOTARY_PASSWORD="${2:-}"; shift 2 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done

# Demo-model triple must be all-or-nothing — partial config silently
# producing a half-broken provider is worse than no provider at all.
DEMO_SET_COUNT=0
[[ -n "$DEMO_URL"   ]] && DEMO_SET_COUNT=$((DEMO_SET_COUNT + 1))
[[ -n "$DEMO_KEY"   ]] && DEMO_SET_COUNT=$((DEMO_SET_COUNT + 1))
[[ -n "$DEMO_MODEL" ]] && DEMO_SET_COUNT=$((DEMO_SET_COUNT + 1))
if [[ "$DEMO_SET_COUNT" -ne 0 && "$DEMO_SET_COUNT" -ne 3 ]]; then
  echo "error: --demo-url, --demo-key, --demo-model must all be set together (or all unset)" >&2
  exit 2
fi

LLAMA_TAG="b8929"
LLAMA_DIST="llama-${LLAMA_TAG}-bin-macos-arm64.tar.gz"
LLAMA_URL="https://github.com/ggerganov/llama.cpp/releases/download/${LLAMA_TAG}/${LLAMA_DIST}"

case "$BUNDLE_LLM" in
  none) LLM_REPO=""; LLM_FILE=""; LLM_NAME="" ;;
  qwen-3b)
    # Qwen2.5-3B-Instruct: ~1.9 GB Q4, native function-calling support — the
    # right tradeoff for Nexus's tool-driven loop. Gemma 3n E2B (the previous
    # default) was ~1.5 GB but couldn't reliably use tools.
    LLM_REPO="bartowski/Qwen2.5-3B-Instruct-GGUF"
    LLM_FILE="Qwen2.5-3B-Instruct-Q4_K_M.gguf"
    LLM_NAME="qwen2.5-3b-instruct"
    ;;
  gemma-e4b)
    LLM_REPO="bartowski/google_gemma-3n-E4B-it-GGUF"
    LLM_FILE="google_gemma-3n-E4B-it-Q4_K_M.gguf"
    LLM_NAME="gemma-3n-e4b"
    ;;
  *) echo "unknown --bundle-llm value: $BUNDLE_LLM (use qwen-3b | gemma-e4b | none)" >&2; exit 2 ;;
esac

[[ -d "$LOOM_DIR" ]] || { echo "loom not found at $LOOM_DIR (set LOOM_DIR=...)" >&2; exit 1; }

echo "==> Cleaning $DIST"
rm -rf "$DIST"
mkdir -p "$STAGE"

echo "==> Building UI (npm run build)"
( cd "$REPO_ROOT/ui" && npm install --no-audit --no-fund && npm run build )
mkdir -p "$STAGE/ui"
cp -R "$REPO_ROOT/ui/dist/." "$STAGE/ui/"

echo "==> Fetching standalone CPython"
PY_CACHE="$DIST/.cache"
mkdir -p "$PY_CACHE"
if [[ ! -f "$PY_CACHE/$PY_DIST" ]]; then
  curl -fsSL -o "$PY_CACHE/$PY_DIST" "$PY_URL"
fi
mkdir -p "$STAGE/python"
tar -xzf "$PY_CACHE/$PY_DIST" -C "$STAGE/python" --strip-components=1
PY="$STAGE/python/bin/python3"
"$PY" --version

echo "==> Installing nexus + dependencies into bundled Python"
"$PY" -m pip install --upgrade pip
# Install the ddgs fork pinned in agent/pyproject.toml's [tool.uv.sources].
# pip doesn't honor [tool.uv.sources], so without this we'd silently fall
# back to upstream PyPI ddgs when loom's [search] extra is resolved below.
"$PY" -m pip install "git+https://github.com/NinoCoelho/ddgs"
# Install loom with the full set of extras nexus pulls in (matches
# loom-framework[anthropic,acp,tui,graphrag,search,scrape] in agent/pyproject.toml).
# PEP 660 editable would point outside the bundle, so do a regular install
# — this copies loom into site-packages.
"$PY" -m pip install "$LOOM_DIR[anthropic,acp,tui,graphrag,search,scrape]"
# Install nexus with all declared deps. [pdf] adds Pillow + fpdf2 so the
# bundled pdf-maker skill works offline. pip sees loom-framework already
# satisfied and won't try to fetch it from PyPI.
"$PY" -m pip install "$REPO_ROOT/agent[pdf]"

# Move site-packages into the staged Resources layout expected by bootstrap.py.
SITE_SRC="$(/usr/bin/find "$STAGE/python/lib" -maxdepth 2 -type d -name 'site-packages' | head -1)"
[[ -n "$SITE_SRC" ]] || { echo "could not locate site-packages" >&2; exit 1; }
mkdir -p "$STAGE/site-packages"
cp -R "$SITE_SRC/." "$STAGE/site-packages/"

echo "==> Stripping .py sources from nexus + loom (bytecode-only)"
# -b emits .pyc next to the .py instead of __pycache__/, so the import system
# still resolves modules after we delete the .py files.
# --invalidation-mode unchecked-hash skips the source-mtime check that would
# normally make orphan .pyc warn at runtime.
"$PY" -m compileall -b -q -f --invalidation-mode unchecked-hash \
  "$STAGE/site-packages/nexus" "$STAGE/site-packages/loom"
/usr/bin/find "$STAGE/site-packages/nexus" "$STAGE/site-packages/loom" \
  -type f -name '*.py' -delete
/usr/bin/find "$STAGE/site-packages/nexus" "$STAGE/site-packages/loom" \
  -type d -name '__pycache__' -exec rm -rf {} +

if [[ "$SKIP_MODELS" -eq 0 ]]; then
  echo "==> Pre-downloading embedding models into bundle"
  NEXUS_MODELS_DIR="$STAGE/models" "$PY" - <<'PYEOF'
import os
from pathlib import Path
models = Path(os.environ["NEXUS_MODELS_DIR"])
(models / "fastembed").mkdir(parents=True, exist_ok=True)
from fastembed import TextEmbedding
TextEmbedding(model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", cache_dir=str(models / "fastembed"))
print("fastembed cached at", models / "fastembed")
PYEOF

  echo "==> Pre-downloading spaCy en_core_web_sm into bundle"
  "$PY" -m spacy download en_core_web_sm
  SPACY_PKG="$("$PY" -c 'import en_core_web_sm, os; print(os.path.dirname(en_core_web_sm.__file__))')"
  mkdir -p "$STAGE/models/spacy"
  cp -R "$SPACY_PKG" "$STAGE/models/spacy/en_core_web_sm_pkg"
  echo "spaCy cached at $STAGE/models/spacy/en_core_web_sm_pkg"

  echo "==> Pre-downloading faster-whisper model into bundle"
  HF_HOME="$STAGE/models/huggingface" "$PY" - <<'PYEOF'
import os
from pathlib import Path
hf = Path(os.environ["HF_HOME"])
hf.mkdir(parents=True, exist_ok=True)
from faster_whisper import download_model
path = download_model("base", cache_dir=str(hf))
print("faster-whisper cached at", path)
PYEOF
fi

if [[ -n "$LLM_REPO" ]]; then
  echo "==> Fetching llama.cpp ($LLAMA_TAG, macos-arm64)"
  LLAMA_CACHE="$DIST/.cache"
  mkdir -p "$LLAMA_CACHE"
  if [[ ! -f "$LLAMA_CACHE/$LLAMA_DIST" ]]; then
    curl -fSL --retry 3 -o "$LLAMA_CACHE/$LLAMA_DIST" "$LLAMA_URL"
  fi
  mkdir -p "$STAGE/llama"
  tar -xzf "$LLAMA_CACHE/$LLAMA_DIST" -C "$STAGE/llama"
  LLAMA_SERVER_REL="$(cd "$STAGE/llama" && /usr/bin/find . -type f -name 'llama-server' -perm -u+x | head -1)"
  [[ -n "$LLAMA_SERVER_REL" ]] || { echo "llama-server not found in archive" >&2; exit 1; }
  echo "llama-server at llama/${LLAMA_SERVER_REL#./}"

  echo "==> Fetching $LLM_NAME GGUF (this is the largest download — minutes on slow links)"
  mkdir -p "$STAGE/models/llm"
  HF_URL="https://huggingface.co/${LLM_REPO}/resolve/main/${LLM_FILE}"
  GGUF_CACHE="$DIST/.cache/$LLM_FILE"
  if [[ ! -f "$GGUF_CACHE" ]]; then
    curl -fSL --retry 3 -o "$GGUF_CACHE" "$HF_URL"
  fi
  cp "$GGUF_CACHE" "$STAGE/models/llm/$LLM_FILE"

  # ctx_size 16384: Nexus's system prompt + tool definitions are ~8K tokens
  # before any conversation, so 4096 returns 400s on every request. Qwen2.5-3B
  # supports 32K natively; 16K fits Nexus prompts with room for several turns
  # while keeping the KV cache around 600 MB.
  cat > "$STAGE/llm.json" <<EOF
{
  "binary": "llama/${LLAMA_SERVER_REL#./}",
  "model_file": "models/llm/${LLM_FILE}",
  "model_name": "${LLM_NAME}",
  "ctx_size": 16384
}
EOF
fi

# Stage bundled skills so they ship with the .app and get seeded into
# ~/.nexus/skills/ on first run (see SkillRegistry._seed_new_builtins).
# Without this, the install on a fresh machine starts with zero skills.
if [[ -d "$REPO_ROOT/skills" ]]; then
  echo "==> Staging bundled skills"
  mkdir -p "$STAGE/skills"
  # rsync skips macOS metadata files (.DS_Store) and the format doc.
  /usr/bin/rsync -a \
    --exclude='.DS_Store' --exclude='SKILL_FORMAT.md' \
    "$REPO_ROOT/skills/" "$STAGE/skills/"
fi

# Stage demo_llm.json when --demo-* / DEMO_LLM_* are set. bootstrap.py reads
# this on launch and seeds ~/.nexus/config.toml ONLY when that file doesn't
# yet exist (true fresh install). Anyone with the .app can read this manifest;
# rely on server-side per-key rate limits + budget caps for abuse protection.
if [[ -n "$DEMO_URL" && -n "$DEMO_KEY" && -n "$DEMO_MODEL" ]]; then
  echo "==> Staging demo_llm.json (model: $DEMO_MODEL)"
  # Build via python -c with env vars to keep the key out of any argv/log,
  # and to get proper JSON escaping for arbitrary key characters.
  DEMO_LLM_BASE_URL="$DEMO_URL" \
  DEMO_LLM_API_KEY="$DEMO_KEY" \
  DEMO_LLM_MODEL="$DEMO_MODEL" \
  DEMO_OUT="$STAGE/demo_llm.json" \
    /usr/bin/env python3 -c '
import json, os
out = {
    "base_url":   os.environ["DEMO_LLM_BASE_URL"],
    "api_key":    os.environ["DEMO_LLM_API_KEY"],
    "model_name": os.environ["DEMO_LLM_MODEL"],
}
with open(os.environ["DEMO_OUT"], "w") as f:
    json.dump(out, f)
'
  chmod 0600 "$STAGE/demo_llm.json"
fi

cp "$PACKAGING/bootstrap.py" "$STAGE/bootstrap.py"
( cd "$LOOM_DIR" && git rev-parse HEAD > "$STAGE/loom_version.txt" 2>/dev/null || true )

echo "==> Building Swift host (SwiftPM)"
SWIFT_PKG="$PACKAGING/macos"
( cd "$SWIFT_PKG" && swift build -c release --arch arm64 )
SWIFT_BIN="$SWIFT_PKG/.build/arm64-apple-macosx/release/Nexus"
[[ -x "$SWIFT_BIN" ]] || { echo "swift build did not produce $SWIFT_BIN" >&2; exit 1; }

echo "==> Assembling $APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$SWIFT_BIN" "$APP/Contents/MacOS/$APP_NAME"
cp "$PACKAGING/macos/Info.plist" "$APP/Contents/Info.plist"
cp "$PACKAGING/macos/Nexus.icns" "$APP/Contents/Resources/Nexus.icns"

RES="$APP/Contents/Resources"
cp -R "$STAGE/python"          "$RES/python"
cp -R "$STAGE/site-packages"   "$RES/site-packages"
cp -R "$STAGE/ui"              "$RES/ui"
[[ -d "$STAGE/models" ]] && cp -R "$STAGE/models" "$RES/models"
[[ -d "$STAGE/llama" ]]  && cp -R "$STAGE/llama"  "$RES/llama"
[[ -d "$STAGE/skills" ]] && cp -R "$STAGE/skills" "$RES/skills"
[[ -f "$STAGE/llm.json" ]] && cp "$STAGE/llm.json" "$RES/llm.json"
[[ -f "$STAGE/demo_llm.json" ]] && { cp "$STAGE/demo_llm.json" "$RES/demo_llm.json"; chmod 0600 "$RES/demo_llm.json"; }
cp "$STAGE/bootstrap.py"       "$RES/bootstrap.py"
[[ -f "$STAGE/loom_version.txt" ]] && cp "$STAGE/loom_version.txt" "$RES/loom_version.txt"

if [[ "$SKIP_SIGN" -eq 0 ]]; then
  if [[ -n "$SIGN_IDENTITY" ]]; then
    echo "==> Codesigning nested binaries with Developer ID: $SIGN_IDENTITY"
    NESTED=0
    while IFS= read -r -d '' bin; do
      codesign --force --sign "$SIGN_IDENTITY" --timestamp "$bin" 2>/dev/null && NESTED=$((NESTED + 1))
    done < <(/usr/bin/find "$APP" \( -name '*.so' -o -name '*.dylib' -o -name '*.abi3.so' \) -type f -print0 | sort -z -u)
    while IFS= read -r -d '' bin; do
      codesign --force --sign "$SIGN_IDENTITY" --options runtime --timestamp "$bin" 2>/dev/null && NESTED=$((NESTED + 1))
    done < <(/usr/bin/find "$APP/Contents/MacOS" "$APP/Contents/Resources/python/bin" -type f -perm -u+x -print0 2>/dev/null | sort -z -u)
    echo "    signed $NESTED nested binaries"

    echo "==> Codesigning bundle with Developer ID: $SIGN_IDENTITY"
    codesign --force --sign "$SIGN_IDENTITY" \
      --options runtime --timestamp "$APP"
  else
    echo "==> Ad-hoc codesigning bundle"
    codesign --force --deep --sign - --timestamp=none "$APP"
  fi
fi

if [[ "$NOTARIZE" -eq 1 ]]; then
  if [[ -z "$SIGN_IDENTITY" ]]; then
    echo "error: --notarize requires --identity (or NEXUS_SIGN_IDENTITY)" >&2
    exit 1
  fi
  if [[ -z "$NOTARY_APPLE_ID" || -z "$NOTARY_TEAM_ID" || -z "$NOTARY_PASSWORD" ]]; then
    echo "error: --notarize requires --notary-apple-id, --notary-team-id, and --notary-password" >&2
    echo "       (or set NOTARY_APPLE_ID, NOTARY_TEAM_ID, NOTARY_PASSWORD env vars)" >&2
    exit 1
  fi

  echo "==> Creating zip for notarization submission"
  NOTARIZE_ZIP="$DIST/Nexus-notarize.zip"
  ditto -c -k --keepParent "$APP" "$NOTARIZE_ZIP"

  echo "==> Submitting for notarization (this may take several minutes)"
  xcrun notarytool submit "$NOTARIZE_ZIP" \
    --apple-id "$NOTARY_APPLE_ID" \
    --team-id "$NOTARY_TEAM_ID" \
    --password "$NOTARY_PASSWORD" \
    --wait

  echo "==> Stapling notarization ticket"
  xcrun stapler staple "$APP"

  rm -f "$NOTARIZE_ZIP"
fi

echo "==> Creating installer package"
PKG="$DIST/Nexus.pkg"
pkgbuild --install-location /Applications --component "$APP" "$PKG"

if [[ -n "$SIGN_IDENTITY" ]]; then
  INSTALLER_IDENTITY="${NEXUS_INSTALLER_IDENTITY:-}"
  if [[ -n "$INSTALLER_IDENTITY" ]]; then
    echo "==> Signing package with Developer ID Installer: $INSTALLER_IDENTITY"
    productsign --sign "$INSTALLER_IDENTITY" "$PKG" "$PKG.signed"
    mv "$PKG.signed" "$PKG"
  fi
fi

echo "==> Done: $APP"
du -sh "$APP"
du -sh "$PKG"
