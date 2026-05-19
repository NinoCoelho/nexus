"""Runtime control of llama-server processes for local LLM inference.

Supports multiple concurrent servers — one per installed GGUF file. Each
running server gets its own port and is registered as ``providers.local-<slug>``
in ``~/.nexus/config.toml`` while the process is alive. State is module-level
so it persists across requests within a server process.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class ServerHandle:
    proc: subprocess.Popen
    port: int
    slug: str
    model_path: Path
    is_embedding: bool = False
    # True when an ``*mmproj*.gguf`` projector sidecar was found alongside
    # the language GGUF and ``--mmproj`` was passed to llama-server. The
    # caller surfaces this through a ``vision`` tag on the [[models]]
    # entry so chat-side capability detection treats the model as
    # vision-capable.
    is_vision: bool = False
    context_window: int = 0


# Architectures (from GGUF metadata `general.architecture`) that produce
# sentence/token embeddings rather than autoregressive chat completions.
# llama-server only exposes /v1/embeddings when launched with --embeddings,
# and chat models refuse to load with that flag — so we must classify
# before spawning.
_EMBEDDING_ARCHITECTURES = {
    "bert",
    "nomic-bert",
    "jina-bert-v2",
    "jina-bert",
    "roberta",
    "xlm-roberta",
    "t5encoder",
    "gritlm",
    "gemma-embedding",
}

# Substrings in `general.architecture` that imply an embedding model even
# when the exact arch isn't in the set above (e.g. future "*-embedding"
# variants). Catches naming conventions like "gemma-embedding", "qwen3-embedding".
_EMBEDDING_ARCH_HINTS = ("embedding", "encoder")

# Filename substrings used as a fallback when GGUF header parsing fails.
_EMBEDDING_NAME_HINTS = (
    "minilm", "bge-", "bge_", "nomic-embed", "e5-", "e5_",
    "gte-", "mxbai-embed", "embed",
)


def _gguf_has_mamba_layers(model_path: Path) -> bool:
    """Check whether a GGUF contains Mamba/SSM layers.

    Hybrid Mamba-Transformer models (e.g. Qwen3-UD, Zamba) include
    ``<arch>.ssm_d_conv`` or similar SSM hyper-parameter keys in their
    metadata. Standard transformer models do not.

    Returns True if SSM/Mamba metadata is found, False otherwise.
    """
    import struct

    arch = _read_gguf_architecture(model_path)
    if not arch:
        return False

    ssm_prefix = f"{arch}.ssm_"
    try:
        with open(model_path, "rb") as f:
            magic = f.read(4)
            if magic != b"GGUF":
                return False
            version = struct.unpack("<I", f.read(4))[0]
            if version < 2:
                return False
            f.read(8)
            kv_count = struct.unpack("<Q", f.read(8))[0]

            for _ in range(min(kv_count, 512)):
                key_len = struct.unpack("<Q", f.read(8))[0]
                if key_len > 4096:
                    return False
                key = f.read(key_len).decode("utf-8", errors="replace")
                val_type = struct.unpack("<I", f.read(4))[0]
                if key.startswith(ssm_prefix):
                    return True
                _read_gguf_value(f, val_type)
        return False
    except Exception:
        return False


def is_mmproj_file(path: Path) -> bool:
    """Vision-language projector sidecar (e.g. ``*mmproj*.gguf``).

    These files aren't models on their own — they pair with a language
    GGUF and feed llama-server through ``--mmproj``. Callers that scan
    ``~/.nexus/models/llm/`` for installable models must skip them so
    each projector doesn't get treated as its own runnable model.
    """
    return "mmproj" in path.name.lower() and path.suffix.lower() == ".gguf"


def find_mmproj_sidecar(model_path: Path) -> Path | None:
    """Return the projector GGUF that pairs with ``model_path``, if any.

    The convention used by community quantizers (e.g.
    ``prithivMLmods/chandra-ocr-2-GGUF``) is to ship a sibling named
    ``<base>.mmproj-<quant>.gguf`` alongside the language GGUF. We look
    in the same directory for any ``*mmproj*.gguf`` and prefer the one
    whose quant suffix matches the language file (``q8_0``, ``f16``,
    etc.); otherwise fall back to the first match.
    """
    parent = model_path.parent
    if not parent.is_dir():
        return None
    candidates = sorted(p for p in parent.glob("*mmproj*.gguf") if p.is_file())
    if not candidates:
        return None
    # Try to match the quant tier of the language GGUF.
    stem_lower = model_path.stem.lower()
    for tier in ("q8_0", "q6_k", "q5_k_m", "q4_k_m", "f16", "bf16", "f32"):
        if tier in stem_lower:
            for c in candidates:
                if tier in c.stem.lower():
                    return c
    return candidates[0]


def _read_gguf_architecture(model_path: Path) -> str | None:
    """Parse the GGUF header and return ``general.architecture`` if present.

    GGUF v2/v3 layout: 4-byte magic 'GGUF', uint32 version, uint64 tensor
    count, uint64 metadata-kv count, then kv pairs: key (uint64 len + utf8
    bytes), value-type (uint32), value (type-dependent). We only need to
    walk far enough to find ``general.architecture`` — typically the very
    first KV pair — so we cap at 256 entries to keep this cheap.

    Returns None on any error so callers can fall back to filename hints.
    """
    import struct

    try:
        with open(model_path, "rb") as f:
            magic = f.read(4)
            if magic != b"GGUF":
                return None
            version = struct.unpack("<I", f.read(4))[0]
            if version < 2:
                return None  # v1 used a different layout; not worth supporting
            f.read(8)  # tensor_count
            kv_count = struct.unpack("<Q", f.read(8))[0]

            for _ in range(min(kv_count, 256)):
                key_len = struct.unpack("<Q", f.read(8))[0]
                if key_len > 4096:
                    return None  # corrupt / not GGUF we recognize
                key = f.read(key_len).decode("utf-8", errors="replace")
                val_type = struct.unpack("<I", f.read(4))[0]
                value = _read_gguf_value(f, val_type)
                if key == "general.architecture" and isinstance(value, str):
                    return value
        return None
    except Exception:
        return None


def _read_gguf_value(f, val_type: int):
    """Read a single GGUF metadata value. Returns None for skipped/array values."""
    import struct

    # 0:u8 1:i8 2:u16 3:i16 4:u32 5:i32 6:f32 7:bool 8:string 9:array
    # 10:u64 11:i64 12:f64
    fixed = {
        0: ("<B", 1), 1: ("<b", 1),
        2: ("<H", 2), 3: ("<h", 2),
        4: ("<I", 4), 5: ("<i", 4), 6: ("<f", 4),
        7: ("<?", 1),
        10: ("<Q", 8), 11: ("<q", 8), 12: ("<d", 8),
    }
    if val_type in fixed:
        fmt, size = fixed[val_type]
        return struct.unpack(fmt, f.read(size))[0]
    if val_type == 8:  # string
        slen = struct.unpack("<Q", f.read(8))[0]
        if slen > 1 << 20:
            raise ValueError("string too long")
        return f.read(slen).decode("utf-8", errors="replace")
    if val_type == 9:  # array — skip past it, we don't need array values here
        elem_type = struct.unpack("<I", f.read(4))[0]
        count = struct.unpack("<Q", f.read(8))[0]
        for _ in range(count):
            _read_gguf_value(f, elem_type)
        return None
    raise ValueError(f"unknown gguf value type {val_type}")


def _read_gguf_context_length(model_path: Path) -> int:
    """Read the context length from GGUF metadata.

    Looks for ``<arch>.context_length`` (e.g. ``qwen2.context_length``,
    ``llama.context_length``), then ``<arch>.max_position_embeddings``,
    then ``general.context_length``.  Returns 0 when none are found.
    """
    import struct

    arch = _read_gguf_architecture(model_path)
    target_keys_ordered: list[str] = []
    if arch:
        target_keys_ordered.append(f"{arch}.context_length")
        target_keys_ordered.append(f"{arch}.max_position_embeddings")
    target_keys_ordered.append("general.context_length")
    target_keys = set(target_keys_ordered)

    try:
        with open(model_path, "rb") as f:
            magic = f.read(4)
            if magic != b"GGUF":
                return 0
            version = struct.unpack("<I", f.read(4))[0]
            if version < 2:
                return 0
            f.read(8)
            kv_count = struct.unpack("<Q", f.read(8))[0]

            for _ in range(min(kv_count, 512)):
                key_len = struct.unpack("<Q", f.read(8))[0]
                if key_len > 4096:
                    return 0
                key = f.read(key_len).decode("utf-8", errors="replace")
                val_type = struct.unpack("<I", f.read(4))[0]
                if key in target_keys:
                    value = _read_gguf_value(f, val_type)
                    if isinstance(value, (int, float)):
                        return int(value)
                    return 0
                _read_gguf_value(f, val_type)
        return 0
    except Exception:
        return 0


def is_embedding_model(model_path: Path) -> bool:
    """Detect whether a GGUF file is an embedding model.

    Primary: read ``general.architecture`` from the GGUF header and match
    against known encoder architectures. Fallback: filename substring
    heuristic for headers we couldn't parse.
    """
    arch = _read_gguf_architecture(model_path)
    if arch is not None:
        a = arch.lower()
        if a in _EMBEDDING_ARCHITECTURES:
            return True
        if any(h in a for h in _EMBEDDING_ARCH_HINTS):
            return True
        return False
    name = model_path.name.lower()
    return any(h in name for h in _EMBEDDING_NAME_HINTS)


# Keyed by GGUF filename (the basename, e.g. "Qwen2.5-7B-Instruct-Q4_K_M.gguf").
_servers: dict[str, ServerHandle] = {}
_VERSION_CACHE: dict[str, int] = {}


def slugify(name: str) -> str:
    """Convert a model stem to a lowercase slug suitable for config IDs."""
    s = re.sub(r"[^a-z0-9]+", "-", name.lower())
    return s.strip("-") or "model"


def is_running(filename: str) -> bool:
    """Return True iff a llama-server is currently up for this GGUF filename."""
    h = _servers.get(filename)
    if h is None:
        return False
    if h.proc.poll() is not None:
        # Process died — clean up.
        _servers.pop(filename, None)
        return False
    return True


def list_running() -> list[dict]:
    """Return a list of currently running servers."""
    out: list[dict] = []
    for filename in list(_servers.keys()):
        if not is_running(filename):
            continue
        h = _servers[filename]
        out.append({
            "filename": filename,
            "slug": h.slug,
            "port": h.port,
            "pid": h.proc.pid,
            "model_path": str(h.model_path),
        })
    return out


def discover_binary() -> Path | None:
    """Locate the llama-server binary.

    Search order:
    1. ``NEXUS_LLAMA_BIN`` environment variable.
    2. Bundled binary relative to ``NEXUS_BUNDLE_DIR`` (macOS .app).
    3. Newest ``llama-server`` found across ``~/.nexus/llama/`` and ``PATH``.

    Returns:
        Path to the binary, or None if not found.
    """
    env_bin = os.environ.get("NEXUS_LLAMA_BIN", "")
    if env_bin:
        p = Path(env_bin)
        if p.is_file():
            return p

    bundle_dir = os.environ.get("NEXUS_BUNDLE_DIR", "")
    if bundle_dir:
        resources = Path(bundle_dir)
        for candidate in resources.glob("llama/**/llama-server"):
            if candidate.is_file():
                return candidate

    candidates: list[tuple[int, Path]] = []

    user_llama = Path.home() / ".nexus" / "llama"
    if user_llama.is_dir():
        for candidate in user_llama.glob("**/llama-server"):
            if candidate.is_file():
                ver = _parse_build_version(candidate)
                candidates.append((ver, candidate))

    which = shutil.which("llama-server")
    if which:
        p = Path(which)
        ver = _parse_build_version(p)
        candidates.append((ver, p))

    if not candidates:
        return None

    candidates.sort(key=lambda t: t[0], reverse=True)
    return candidates[0][1]


def _parse_build_version(binary: Path) -> int:
    """Extract the numeric build version from a llama-server path.

    Looks for a ``b<digits>`` pattern in the parent directory name first
    (e.g. ``llama-b8929``), then in the binary name itself.  Returns 0 if
    no version can be determined.  Results are cached per resolved path.
    """
    key = str(binary.resolve())
    if key in _VERSION_CACHE:
        return _VERSION_CACHE[key]
    ver = 0
    for text in [binary.parent.name, binary.name]:
        m = re.search(r"b(\d+)", text)
        if m:
            ver = int(m.group(1))
            break
    _VERSION_CACHE[key] = ver
    return ver


def start(model_path: Path, ctx_size: int = 16384) -> ServerHandle:
    """Start (or return existing) llama-server for the given model.

    If a server is already running for ``model_path.name``, returns its handle
    without spawning a duplicate.

    Raises:
        RuntimeError: If the binary is missing or the server fails to become
            ready within 90 seconds.
    """
    filename = model_path.name
    if is_running(filename):
        return _servers[filename]

    binary = discover_binary()
    if binary is None:
        raise RuntimeError(
            "llama-server binary not found. Set NEXUS_LLAMA_BIN, install "
            "llama.cpp, or place the binary under ~/.nexus/llama/.",
        )

    port = _pick_free_port()
    slug = slugify(model_path.stem)
    is_emb = is_embedding_model(model_path)

    if not is_emb and _gguf_has_mamba_layers(model_path):
        raise RuntimeError(
            "This model uses a hybrid Mamba/Transformer architecture that is not "
            "yet supported by llama.cpp. Look for a standard (non-hybrid) GGUF — "
            "avoid filenames with \"UD\", \"MTP\", or \"Mamba\" in the name. "
            "For Qwen models, choose repos labeled just \"Qwen3-27B-GGUF\" or "
            "\"Qwen3.6-27B-GGUF\" without UD/MTP variants."
        )

    detected_ctx = _read_gguf_context_length(model_path)
    if detected_ctx > 0 and (ctx_size <= 0 or detected_ctx > ctx_size):
        if ctx_size > 0:
            log.info(
                "[local_llm] GGUF context_length=%d overrides stale config value %d for %s",
                detected_ctx, ctx_size, model_path.name,
            )
        else:
            log.info(
                "[local_llm] auto-detected context_length=%d from GGUF metadata for %s",
                detected_ctx, model_path.name,
            )
        ctx_size = detected_ctx
    elif ctx_size <= 0:
        ctx_size = 32768
        log.info(
            "[local_llm] GGUF context_length not found; using fallback %d for %s",
            ctx_size, model_path.name,
        )

    log_path = Path.home() / "Library" / "Logs" / "Nexus" / "llama-server.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = open(log_path, "ab")

    cmd = [
        str(binary),
        "-m", str(model_path),
        "--host", "127.0.0.1",
        "--port", str(port),
        "-c", str(ctx_size),
        "-ngl", "99",
    ]
    mmproj_path: Path | None = None
    if is_emb:
        # Embedding-only mode: chat-template flags (--jinja) are incompatible
        # with --embeddings on most builds, and the OpenAI-compat
        # /v1/embeddings route only exists when this flag is set.
        cmd += ["--embeddings", "--pooling", "mean"]
    else:
        # Vision-language models (e.g. chandra-ocr-2, llava-style) ship a
        # projector sidecar that llama-server loads via --mmproj.
        # Without it, image inputs get rejected with "image input is not
        # supported - hint: ... mmproj". When we find a sibling, wire it up
        # — chat-only GGUFs without a sidecar are unaffected.
        mmproj_path = find_mmproj_sidecar(model_path)
        if mmproj_path is not None:
            cmd += ["--mmproj", str(mmproj_path)]
        # `--jinja` enables the chat template so tool-calling and reasoning
        # routing work as the model expects. We let `--reasoning-format`
        # default to `auto`: for thinking models (DeepSeek-R1, QwQ, GLM
        # `<think>…</think>`) llama.cpp routes the CoT to
        # `delta.reasoning_content` and the post-think content to
        # `delta.content`. Inlining the CoT (the previous behavior) made the
        # CoT leak into the assistant message and broke llama.cpp's tool-call
        # parser, which only scans the post-think tail. The Nexus UI renders
        # the separate `thinking` SSE channel as a collapsed section.
        cmd += ["--jinja"]
    is_vision = mmproj_path is not None

    log.info("[local_llm] starting llama-server: %s", " ".join(cmd))
    proc = subprocess.Popen(cmd, stdout=log_handle, stderr=subprocess.STDOUT)

    health_url = f"http://127.0.0.1:{port}/v1/models"
    deadline = time.time() + 90.0
    import urllib.error
    import urllib.request

    while time.time() < deadline:
        if proc.poll() is not None:
            log.warning("[local_llm] llama-server exited early; see %s", log_path)
            raise RuntimeError(_diagnose_start_failure(log_path, model_path))
        try:
            with urllib.request.urlopen(health_url, timeout=1.5) as r:
                if r.status == 200:
                    handle = ServerHandle(
                        proc=proc, port=port, slug=slug, model_path=model_path,
                        is_embedding=is_emb, is_vision=is_vision,
                        context_window=ctx_size,
                    )
                    _servers[filename] = handle

                    actual_ctx = _query_server_n_ctx(port)
                    if actual_ctx > 0:
                        handle.context_window = actual_ctx

                    _MIN_CTX_WARNING = 24_576
                    if not is_emb and handle.context_window > 0 and handle.context_window < _MIN_CTX_WARNING:
                        log.warning(
                            "[local_llm] context window (%d) may be too small for the "
                            "agent's system prompt + tool definitions (~22K tokens). "
                            "Consider using a model with at least 32K context.",
                            handle.context_window,
                        )

                    log.info(
                        "[local_llm] llama-server ready: %s on :%d (slug=%s, ctx=%d)",
                        filename, port, slug, handle.context_window,
                    )
                    return handle
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.5)

    proc.terminate()
    raise RuntimeError(_diagnose_start_failure(log_path, model_path))


def _query_server_n_ctx(port: int) -> int:
    """Query llama-server ``/props`` for the actual ``n_ctx`` in use.

    Returns 0 if the endpoint is unavailable or the value cannot be parsed.
    """
    import json
    import urllib.error
    import urllib.request

    try:
        url = f"http://127.0.0.1:{port}/props"
        with urllib.request.urlopen(url, timeout=2) as r:
            if r.status == 200:
                data = json.loads(r.read())
                return int(
                    data.get("default_generation_settings", {}).get("n_ctx", 0)
                )
    except Exception:
        pass
    return 0


def _diagnose_start_failure(log_path: Path, model_path: Path) -> str:
    """Read the tail of the llama-server log and produce a user-facing message.

    Returns a string like ``"llama-server failed: <reason>. <hint>"``.
    """
    try:
        text = log_path.read_text(errors="replace")
    except OSError:
        return f"llama-server failed to start. Log: {log_path}"

    missing_match = re.search(r"missing tensor '([^']+)'", text)
    if missing_match:
        tensor = missing_match.group(1)
        arch = _read_gguf_architecture(model_path)
        arch_label = f" (architecture: {arch})" if arch else ""
        return (
            f"This model{arch_label} is not compatible with the installed version of "
            f"llama.cpp. It requires layers (e.g. {tensor}) that your build does not "
            f"support. Try updating llama.cpp first (Settings → Local Models). If the "
            f"update doesn't help, this architecture may not be supported yet — use a "
            f"standard (non-hybrid) GGUF without \"UD\" or \"Mamba\" in the filename."
        )

    if "exceeds the available context size" in text:
        return (
            "Model context window is too small for the agent's system prompt + tools. "
            "Try a model with at least 32K context, or update llama.cpp to a newer build."
        )

    if "CUDA out of memory" in text or "MPS out of memory" in text:
        return (
            "Not enough GPU/memory to load this model. Try a smaller quantization "
            "(e.g. Q4_K_M instead of Q8_0) or a smaller model."
        )

    if "failed to load model" in text:
        return (
            "llama-server could not load this GGUF file. It may be corrupted or use "
            "an unsupported format. Try re-downloading the model or choosing a "
            "different quantization."
        )

    return f"llama-server failed to start. Check the log for details: {log_path}"


def stop(filename: str) -> bool:
    """Terminate the llama-server for the given GGUF filename.

    Returns:
        True if a server was running and stopped; False if nothing was up.
    """
    h = _servers.pop(filename, None)
    if h is None:
        return False
    try:
        h.proc.terminate()
        try:
            h.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            h.proc.kill()
            h.proc.wait()
    except Exception:
        log.exception("[local_llm] error stopping llama-server for %s", filename)
    return True


def stop_all() -> None:
    """Stop every running llama-server. Used during graceful shutdown."""
    for filename in list(_servers.keys()):
        stop(filename)


# ---------------------------------------------------------------------------
# Config integration
# ---------------------------------------------------------------------------

def _provider_name(slug: str) -> str:
    return f"local-{slug}"


def _load_config() -> tuple[Path, dict]:
    import tomli_w  # noqa: F401  (used by callers via _save_config)
    try:
        import tomllib
    except ImportError:  # pragma: no cover
        import tomli as tomllib  # type: ignore

    cfg_path = Path.home() / ".nexus" / "config.toml"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)

    raw: dict = {}
    if cfg_path.is_file():
        try:
            with open(cfg_path, "rb") as f:
                raw = tomllib.load(f)
        except (OSError, ValueError):
            raw = {}
    return cfg_path, raw


def _save_config(cfg_path: Path, raw: dict) -> None:
    import tomli_w
    with open(cfg_path, "wb") as f:
        tomli_w.dump(raw, f)


def add_to_config(
    slug: str,
    port: int,
    is_embedding: bool = False,
    context_window: int = 0,
    is_vision: bool = False,
) -> str:
    """Register a running local model in ``~/.nexus/config.toml``.

    Adds (or refreshes) ``providers.local-<slug>`` and a corresponding
    ``[[models]]`` entry. Preserves all other providers, models, and settings.

    Embedding models register as ``openai_compat`` (llama.cpp's
    ``/v1/embeddings`` endpoint) and are tagged ``is_embedding_capable=true``
    so the UI's embedding-role selector accepts them.

    Returns:
        The model id (``"local-<slug>/<slug>"``).
    """
    cfg_path, raw = _load_config()

    providers = raw.setdefault("providers", {})
    pname = _provider_name(slug)
    # Embedding models are addressed as openai_compat so they hit
    # llama-server's /v1/embeddings — base_url must include /v1 because
    # OpenAIEmbeddingProvider POSTs to ``{base_url}/embeddings`` directly.
    # Chat models register as "ollama"; the registry adds /v1 itself for that
    # branch, so the bare host is correct there.
    base_url = (
        f"http://127.0.0.1:{port}/v1"
        if is_embedding
        else f"http://127.0.0.1:{port}"
    )
    providers[pname] = {
        "base_url": base_url,
        "api_key_env": "",
        "use_inline_key": False,
        "type": "openai_compat" if is_embedding else "ollama",
    }

    # Drop legacy unified `local` provider from the singleton era — it was a
    # different model and would be misregistered now.
    providers.pop("local", None)

    model_id = f"{pname}/{slug}"
    models = list(raw.get("models", []))
    # Preserve user-configured context_window across Stop/Start cycles —
    # the entry is rebuilt below, so capture the prior value first.
    if context_window <= 0:
        for m in models:
            if m.get("id") == model_id and isinstance(m.get("context_window"), int):
                context_window = m["context_window"]
                break
    # Drop any prior entries for this exact slug; preserve unrelated local-* entries.
    models = [
        m for m in models
        if not (m.get("provider") == pname or m.get("id") == model_id)
    ]
    # Also clean up legacy ``provider == "local"`` entries from the singleton era.
    models = [m for m in models if m.get("provider") != "local"]
    if is_embedding:
        tags = ["local", "offline", "embedding"]
    else:
        tags = ["local", "offline"]
        if is_vision:
            tags.append("vision")
    entry: dict = {
        "id": model_id,
        "provider": pname,
        "model_name": slug,
        "tags": tags,
        "tier": "fast",
        "notes": (
            "Local embedding model served by llama.cpp."
            if is_embedding
            else "Local GGUF model running via llama.cpp."
        ),
    }
    if is_embedding:
        entry["is_embedding_capable"] = True
    if context_window > 0:
        entry["context_window"] = context_window
    models.append(entry)
    raw["models"] = models

    agent_cfg = raw.setdefault("agent", {})
    cur_default = agent_cfg.get("default_model", "")
    # Only seed default_model if empty or pointing at a stale local-* model id
    # that's no longer in our models list. Never auto-default to an embedding
    # model — those can't serve chat.
    valid_ids = {m["id"] for m in models}
    if not is_embedding and (
        not cur_default or (
            cur_default.startswith(("local/", "local-")) and cur_default not in valid_ids
        )
    ):
        agent_cfg["default_model"] = model_id

    _save_config(cfg_path, raw)
    log.info("[local_llm] added to config: %s @ :%d (embedding=%s)", pname, port, is_embedding)
    return model_id


def remove_from_config(slug: str) -> None:
    """Remove ``providers.local-<slug>`` and its corresponding model entry."""
    cfg_path, raw = _load_config()
    pname = _provider_name(slug)

    providers = raw.get("providers", {})
    providers.pop(pname, None)

    models = list(raw.get("models", []))
    raw["models"] = [m for m in models if m.get("provider") != pname]

    # If the stopped model was the default, clear it so the picker falls back.
    agent_cfg = raw.get("agent", {})
    cur_default = agent_cfg.get("default_model", "")
    if cur_default == f"{pname}/{slug}":
        agent_cfg["default_model"] = ""

    _save_config(cfg_path, raw)
    log.info("[local_llm] removed from config: %s", pname)


def cleanup_stale_config() -> None:
    """Drop config entries for local-* providers whose servers aren't running.

    Called at server startup to recover from a previous crash where the
    daemon died with running entries left in config.
    """
    cfg_path, raw = _load_config()
    providers = raw.get("providers", {})
    models = raw.get("models", [])

    running_pnames = {_provider_name(h.slug) for h in _servers.values()}

    changed = False
    for pname in list(providers.keys()):
        if pname == "local" or pname.startswith("local-"):
            if pname not in running_pnames:
                providers.pop(pname, None)
                changed = True

    new_models = [
        m for m in models
        if not (
            m.get("provider") == "local"
            or (
                isinstance(m.get("provider"), str)
                and m["provider"].startswith("local-")
                and m["provider"] not in running_pnames
            )
        )
    ]
    if len(new_models) != len(models):
        raw["models"] = new_models
        changed = True

    agent_cfg = raw.get("agent", {})
    cur_default = agent_cfg.get("default_model", "")
    valid_ids = {m["id"] for m in raw.get("models", [])}
    if cur_default and cur_default.startswith(("local/", "local-")) and cur_default not in valid_ids:
        agent_cfg["default_model"] = ""
        changed = True

    if changed:
        _save_config(cfg_path, raw)
        log.info("[local_llm] cleaned up stale local-* entries")


def restart_local_models(models_dir: Path | None = None) -> int:
    """Restart every local-* provider previously registered in config.

    Called at server startup so a model the user enabled in a prior run is
    available for chat as soon as the daemon comes up — no need to open
    Settings to spawn its llama-server. Each restarted model gets a fresh
    port (the previous run's port is gone with the dead process); we refresh
    its config entry via ``add_to_config`` so the registry sees the live URL.

    For each ``providers.local-<slug>`` in config, we look up the matching
    GGUF in ``models_dir`` (default: ``~/.nexus/models/llm/``) by slugifying
    the file stem. Missing GGUF, missing binary, or a server that fails to
    come up → the entry is removed instead of left to dangle.

    Returns:
        Count of models successfully (re)started.
    """
    cfg_path, raw = _load_config()
    providers = raw.get("providers", {}) or {}
    models = raw.get("models", []) or []

    targets: list[tuple[str, int]] = []  # (slug, configured_ctx)
    for pname in list(providers.keys()):
        if not pname.startswith("local-") or pname == "local":
            continue
        slug = pname[len("local-"):]
        if not slug:
            continue
        ctx = 0
        for m in models:
            if m.get("provider") == pname and isinstance(m.get("context_window"), int):
                ctx = m["context_window"]
                break
        targets.append((slug, ctx))

    if not targets:
        return 0

    mdir = models_dir or (Path.home() / ".nexus" / "models" / "llm")
    # Filter out mmproj sidecars — those aren't runnable models.
    ggufs: list[Path] = (
        sorted(p for p in mdir.glob("*.gguf") if not is_mmproj_file(p))
        if mdir.is_dir()
        else []
    )
    by_slug = {slugify(p.stem): p for p in ggufs}

    started = 0
    for slug, ctx in targets:
        gguf = by_slug.get(slug)
        if gguf is None:
            log.warning("[local_llm] no GGUF found for slug %r — removing from config", slug)
            remove_from_config(slug)
            continue
        try:
            handle = start(gguf, ctx_size=ctx) if ctx > 0 else start(gguf)
        except RuntimeError as exc:
            log.warning(
                "[local_llm] failed to restart %s (%s) — removing from config: %s",
                slug, gguf.name, exc,
            )
            remove_from_config(slug)
            continue
        add_to_config(
            handle.slug, handle.port,
            is_embedding=handle.is_embedding,
            is_vision=handle.is_vision,
            context_window=handle.context_window or ctx,
        )
        started += 1
        log.info("[local_llm] restarted %s on :%d", slug, handle.port)

    return started


def reap_orphans() -> list[int]:
    """Kill llama-server processes whose parent has died (PPID=1).

    When the Nexus daemon crashes or is force-killed, its child
    llama-server processes survive with PPID=1 (adopted by launchd/init).
    On the next startup ``restart_local_models`` spawns fresh servers
    on new ports, leaving the old ones consuming GPU/RAM indefinitely.

    This function scans the process table for ``llama-server`` binaries
    under ``~/.nexus/llama/`` whose parent is not the current process
    (or PID 1) and terminates them.  Only processes launched from the
    user's own ``~/.nexus/llama/`` directory are considered — a system
    ``llama-server`` started manually by the user is left alone.

    Returns:
        List of PIDs that were reaped.
    """
    import psutil

    reaped: list[int] = []
    my_pid = os.getpid()
    llama_prefix = str(Path.home() / ".nexus" / "llama")

    try:
        for proc in psutil.process_iter(["pid", "ppid", "exe", "cmdline"]):
            try:
                exe = proc.info.get("exe") or ""
                if llama_prefix not in exe:
                    continue
                ppid = proc.info.get("ppid", 0)
                if ppid == my_pid:
                    continue  # our own child — keep it
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

            # This is an orphan — parent is dead (PPID re-parented to 1)
            # or belongs to a different Nexus instance.
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except (psutil.NoSuchProcess, psutil.TimeoutExpired):
                try:
                    proc.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            reaped.append(proc.pid)
            log.info("[local_llm] reaped orphan llama-server PID %d", proc.pid)
    except Exception:
        log.exception("[local_llm] error during orphan reaping")

    return reaped


def _pick_free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]
