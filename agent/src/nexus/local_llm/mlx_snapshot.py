"""Read an MLX model snapshot off disk: capabilities, context window, size.

An "installed MLX model" is a Hugging Face snapshot directory under
``~/.nexus/models/mlx/<slug>/`` — unlike llama.cpp, where the unit of identity
is a single ``.gguf`` file. Everything we need is in that directory, offline:

* ``config.json`` → architecture, context window, quantization, vision-ness
* ``tokenizer_config.json`` / ``chat_template.jinja`` → the chat template, from
  which we sniff tool-call support and reasoning sentinels
* ``*.safetensors`` → the on-disk weight size

This is why MLX models need **no capability probe**: the truth is local.
:func:`read_snapshot` feeds ``nexus.local_llm.mlx_manager.add_to_config``, which
writes ``context_window`` and ``tags`` onto the ``[[models]]`` config entry —
both of which have first-priority overrides in the resolution chain (see
``_builder._model_context_window`` and ``catalog._user_config_capabilities``).

Pure stdlib: no mlx import, so this is testable on any OS.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

__all__ = ["MlxModelInfo", "read_snapshot", "is_mlx_snapshot", "slug_for"]


# Architectures that are vision-language models even when ``vision_config`` is
# absent from the top level of config.json.
_VLM_ARCH_MARKERS: tuple[str, ...] = (
    "vl", "vlm", "llava", "idefics", "pixtral", "molmo", "moondream",
    "minicpmv", "minicpm_v", "internvl", "paligemma", "qwen2vl", "qwen2_vl",
    "gemma3", "smolvlm", "got_ocr", "deepseek_vl", "dots_ocr", "florence",
)

# Reasoning-model sentinels in a chat template.
_THINK_MARKERS: tuple[str, ...] = ("<think>", "<thinking>", "</think>", "</thinking>")

_SAFETENSORS_GLOB = "*.safetensors"


@dataclass
class MlxModelInfo:
    """Everything we can learn about a snapshot without loading it."""

    repo_id: str
    local_dir: Path
    architectures: list[str] = field(default_factory=list)
    model_type: str = ""
    max_position_embeddings: int = 0
    quantization_bits: int = 0
    is_vlm: bool = False
    supports_tools: bool = False
    capabilities: list[str] = field(default_factory=list)
    chat_template: str = ""
    total_bytes: int = 0

    @property
    def slug(self) -> str:
        return slug_for(self.repo_id)

    @property
    def total_gb(self) -> float:
        return round(self.total_bytes / 1_000_000_000, 2)

    def to_public(self) -> dict[str, Any]:
        """JSON-safe shape for the ``/local/mlx/*`` routes.

        ``chat_template`` is deliberately omitted — it can be tens of KB and
        no client needs it.
        """
        return {
            "repo_id": self.repo_id,
            "slug": self.slug,
            "local_dir": str(self.local_dir),
            "architectures": list(self.architectures),
            "model_type": self.model_type,
            "context_window": self.max_position_embeddings,
            "quantization_bits": self.quantization_bits,
            "is_vlm": self.is_vlm,
            "supports_tools": self.supports_tools,
            "capabilities": list(self.capabilities),
            "total_bytes": self.total_bytes,
            "total_gb": self.total_gb,
        }


def slug_for(repo_id: str) -> str:
    """``"mlx-community/Qwen3-30B-A3B-4bit"`` → ``"qwen3-30b-a3b-4bit"``.

    Drops the owner, lowercases, and collapses anything that is not
    ``[a-z0-9.]`` to a single dash so the result is safe as a config key, a
    provider-name suffix and a directory name.
    """
    tail = (repo_id or "").split("/")[-1].strip().lower()
    slug = re.sub(r"[^a-z0-9.]+", "-", tail).strip("-")
    return slug or "model"


def is_mlx_snapshot(directory: Path | str) -> bool:
    """Cheap check: a directory with a ``config.json`` and some weights."""
    d = Path(directory)
    if not d.is_dir():
        return False
    if not (d / "config.json").is_file():
        return False
    return any(d.glob(_SAFETENSORS_GLOB))


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_chat_template(directory: Path, tokenizer_cfg: dict[str, Any]) -> str:
    """Chat template from whichever of the three places holds it.

    Newer repos ship ``chat_template.jinja``; older ones inline it in
    ``tokenizer_config.json``. A list value (some repos ship named templates)
    is flattened so sniffing still works.
    """
    jinja = directory / "chat_template.jinja"
    if jinja.is_file():
        try:
            return jinja.read_text(encoding="utf-8")
        except OSError:
            pass

    tpl = tokenizer_cfg.get("chat_template")
    if isinstance(tpl, str):
        return tpl
    if isinstance(tpl, list):
        parts: list[str] = []
        for entry in tpl:
            if isinstance(entry, dict) and isinstance(entry.get("template"), str):
                parts.append(entry["template"])
            elif isinstance(entry, str):
                parts.append(entry)
        return "\n".join(parts)
    return ""


def _context_window(cfg: dict[str, Any]) -> int:
    """Context window from config.json, through the usual aliases.

    Order: top-level ``max_position_embeddings``, then the ``text_config``
    nesting that VLM configs use, then ``n_positions`` / ``max_seq_len`` /
    ``model_max_length``, then a rope-scaled base. Returns 0 when unknown so
    callers fall back to the shared resolution chain rather than asserting a
    wrong number.
    """
    nodes: list[dict[str, Any]] = [cfg]
    for key in ("text_config", "llm_config", "language_config"):
        nested = cfg.get(key)
        if isinstance(nested, dict):
            nodes.append(nested)

    for node in nodes:
        for key in ("max_position_embeddings", "n_positions", "max_seq_len", "model_max_length"):
            value = node.get(key)
            if isinstance(value, (int, float)) and value > 0:
                base = int(value)
                scaling = node.get("rope_scaling")
                if isinstance(scaling, dict):
                    factor = scaling.get("factor")
                    # Only trust a factor that scales up; "rope_type: default"
                    # and factor<=1 entries mean the base is already final.
                    if isinstance(factor, (int, float)) and factor > 1:
                        return int(base * float(factor))
                return base
    return 0


def _quantization_bits(cfg: dict[str, Any]) -> int:
    quant = cfg.get("quantization")
    if isinstance(quant, dict):
        bits = quant.get("bits")
        if isinstance(bits, (int, float)) and bits > 0:
            return int(bits)
    bits = cfg.get("bits")
    if isinstance(bits, (int, float)) and bits > 0:
        return int(bits)
    return 0


def _is_vlm(cfg: dict[str, Any], architectures: list[str], model_type: str) -> bool:
    if isinstance(cfg.get("vision_config"), dict):
        return True
    haystack = " ".join([*(a.lower() for a in architectures), (model_type or "").lower()])
    return any(marker in haystack for marker in _VLM_ARCH_MARKERS)


def _supports_tools(chat_template: str) -> bool:
    """Whether the chat template can accept a ``tools`` argument.

    Two independent signals, either sufficient: the template references a
    ``tools`` variable, or it contains a tool-call sentinel we recognize. Both
    are needed because some templates render tool calls without ever naming the
    variable ``tools``.
    """
    from ..agent.llm.mlx_support import sniff_tool_sentinels

    tpl = chat_template or ""
    if re.search(r"\btools\b", tpl):
        return True
    opens, _closes = sniff_tool_sentinels(tpl)
    return bool(opens)


def read_snapshot(local_dir: Path | str, *, repo_id: str | None = None) -> MlxModelInfo:
    """Inspect a snapshot directory. Never raises on malformed metadata.

    ``repo_id`` defaults to a reconstruction from the directory name, which is
    what :func:`nexus.local_llm.mlx_manager.list_installed` wants; the
    downloader passes the real value through so the recorded id is exact.
    """
    directory = Path(local_dir)
    cfg = _read_json(directory / "config.json")
    tokenizer_cfg = _read_json(directory / "tokenizer_config.json")
    chat_template = _read_chat_template(directory, tokenizer_cfg)

    raw_archs = cfg.get("architectures")
    architectures = [a for a in raw_archs if isinstance(a, str)] if isinstance(raw_archs, list) else []
    model_type = cfg.get("model_type") if isinstance(cfg.get("model_type"), str) else ""

    total_bytes = 0
    if directory.is_dir():
        for shard in directory.glob(_SAFETENSORS_GLOB):
            try:
                total_bytes += shard.stat().st_size
            except OSError:
                continue

    is_vlm = _is_vlm(cfg, architectures, model_type)
    supports_tools = _supports_tools(chat_template)

    capabilities = ["chat"]
    if supports_tools:
        capabilities.append("tools")
    if is_vlm:
        capabilities.append("vision")
    if any(marker in chat_template for marker in _THINK_MARKERS):
        capabilities.append("reasoning")

    return MlxModelInfo(
        repo_id=repo_id or f"mlx-community/{directory.name}",
        local_dir=directory,
        architectures=architectures,
        model_type=model_type,
        max_position_embeddings=_context_window(cfg),
        quantization_bits=_quantization_bits(cfg),
        is_vlm=is_vlm,
        supports_tools=supports_tools,
        capabilities=capabilities,
        chat_template=chat_template,
        total_bytes=total_bytes,
    )
