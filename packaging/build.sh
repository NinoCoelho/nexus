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
#   packaging/build.sh --bundle-llm gemma-e2b   # bundle a local LLM (default)
#   packaging/build.sh --bundle-llm gemma-e4b   # larger Gemma 3n variant (~2.5 GB)
#   packaging/build.sh --bundle-llm none        # no local LLM (smaller bundle)
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
BUNDLE_LLM="gemma-e2b"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-models) SKIP_MODELS=1; shift ;;
    --skip-sign)   SKIP_SIGN=1; shift ;;
    --bundle-llm)  BUNDLE_LLM="${2:-}"; shift 2 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done

LLAMA_TAG="b8929"
LLAMA_DIST="llama-${LLAMA_TAG}-bin-macos-arm64.tar.gz"
LLAMA_URL="https://github.com/ggerganov/llama.cpp/releases/download/${LLAMA_TAG}/${LLAMA_DIST}"

case "$BUNDLE_LLM" in
  none) LLM_REPO=""; LLM_FILE=""; LLM_NAME="" ;;
  gemma-e2b)
    LLM_REPO="bartowski/google_gemma-3n-E2B-it-GGUF"
    LLM_FILE="google_gemma-3n-E2B-it-Q4_K_M.gguf"
    LLM_NAME="gemma-3n-e2b"
    ;;
  gemma-e4b)
    LLM_REPO="bartowski/google_gemma-3n-E4B-it-GGUF"
    LLM_FILE="google_gemma-3n-E4B-it-Q4_K_M.gguf"
    LLM_NAME="gemma-3n-e4b"
    ;;
  *) echo "unknown --bundle-llm value: $BUNDLE_LLM (use gemma-e2b | gemma-e4b | none)" >&2; exit 2 ;;
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
# Install loom first (PEP 660 editable would point outside the bundle, so do a
# regular install from the local path — this copies it into site-packages).
"$PY" -m pip install "$LOOM_DIR"
# Install nexus without re-resolving loom (already satisfied).
"$PY" -m pip install --no-deps "$REPO_ROOT/agent"
# Install nexus's deps (loom-framework already satisfied; pip will skip it).
"$PY" -m pip install \
  "uvicorn[standard]>=0.29" "openai>=1.50" "anthropic>=0.40" tomli_w \
  "psutil>=5.9" "python-multipart>=0.0.26" "fastembed>=0.4" "spacy>=3.8" \
  "faster-whisper>=1.0"

# Move site-packages into the staged Resources layout expected by bootstrap.py.
SITE_SRC="$(/usr/bin/find "$STAGE/python/lib" -maxdepth 2 -type d -name 'site-packages' | head -1)"
[[ -n "$SITE_SRC" ]] || { echo "could not locate site-packages" >&2; exit 1; }
mkdir -p "$STAGE/site-packages"
cp -R "$SITE_SRC/." "$STAGE/site-packages/"

if [[ "$SKIP_MODELS" -eq 0 ]]; then
  echo "==> Pre-downloading embedding models into bundle"
  NEXUS_MODELS_DIR="$STAGE/models" "$PY" - <<'PYEOF'
import os
from pathlib import Path
models = Path(os.environ["NEXUS_MODELS_DIR"])
(models / "fastembed").mkdir(parents=True, exist_ok=True)
from fastembed import TextEmbedding
TextEmbedding(model_name="BAAI/bge-small-en-v1.5", cache_dir=str(models / "fastembed"))
print("fastembed cached at", models / "fastembed")
PYEOF

  echo "==> Pre-downloading spaCy en_core_web_sm into bundle"
  "$PY" -m spacy download en_core_web_sm
  SPACY_PKG="$("$PY" -c 'import en_core_web_sm, os; print(os.path.dirname(en_core_web_sm.__file__))')"
  mkdir -p "$STAGE/models/spacy"
  cp -R "$SPACY_PKG" "$STAGE/models/spacy/en_core_web_sm_pkg"
  echo "spaCy cached at $STAGE/models/spacy/en_core_web_sm_pkg"
  # faster-whisper warmup is optional; skip unless explicitly requested.
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

  cat > "$STAGE/llm.json" <<EOF
{
  "binary": "llama/${LLAMA_SERVER_REL#./}",
  "model_file": "models/llm/${LLM_FILE}",
  "model_name": "${LLM_NAME}",
  "ctx_size": 4096
}
EOF
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

RES="$APP/Contents/Resources"
cp -R "$STAGE/python"          "$RES/python"
cp -R "$STAGE/site-packages"   "$RES/site-packages"
cp -R "$STAGE/ui"              "$RES/ui"
[[ -d "$STAGE/models" ]] && cp -R "$STAGE/models" "$RES/models"
[[ -d "$STAGE/llama" ]]  && cp -R "$STAGE/llama"  "$RES/llama"
[[ -f "$STAGE/llm.json" ]] && cp "$STAGE/llm.json" "$RES/llm.json"
cp "$STAGE/bootstrap.py"       "$RES/bootstrap.py"
[[ -f "$STAGE/loom_version.txt" ]] && cp "$STAGE/loom_version.txt" "$RES/loom_version.txt"

if [[ "$SKIP_SIGN" -eq 0 ]]; then
  echo "==> Ad-hoc codesigning bundle"
  # python-build-standalone's interpreter and dylibs already ship with
  # consistent ad-hoc signatures. Re-signing them with --options runtime
  # (hardened runtime) caused Team-ID mismatches between python3 and
  # libpython3.12.dylib. Sign only the outer .app with --deep, which
  # re-signs the Nexus host binary and leaves the bundled Python tree's
  # existing self-consistent signatures intact for files we don't touch.
  codesign --force --deep --sign - --timestamp=none "$APP"
fi

echo "==> Done: $APP"
du -sh "$APP"
