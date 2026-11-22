"""Pure helpers for the in-process MLX provider.

This module deliberately imports **nothing from mlx**. Everything here is
plain-stdlib logic so the full test suite exercises it on any OS, while
``mlx_provider`` / ``mlx_runtime`` keep their ``import mlx`` calls lazy and
mac-only. Keep it that way: if you need an mlx type here, you are in the
wrong module.

Three jobs:

* :class:`SpanSplitter` — incremental scanner that routes a flat token stream
  into prose / reasoning / tool-call channels. MLX hands us one undifferentiated
  text stream (unlike OpenAI-compat, which separates ``delta.reasoning`` and
  ``delta.tool_calls`` for us), so this provider is the only one that has to do
  the splitting itself.
* :func:`select_tool_parser_name` — picks an ``mlx_lm.tool_parsers`` module for
  a loaded model from its architecture, falling back to sniffing the chat
  template.
* :func:`estimate_resident_gb` / :func:`fits_in_unified_memory` — "will this
  model fit" sizing, so an oversized activation is refused at the route with
  real numbers instead of thrashing the machine at generation time.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

__all__ = [
    "Channel",
    "SpanSplitter",
    "DEFAULT_OPEN_THINK",
    "DEFAULT_CLOSE_THINK",
    "TOOL_SENTINELS",
    "sniff_tool_sentinels",
    "select_tool_parser_name",
    "template_opens_thinking",
    "estimate_resident_gb",
    "fits_in_unified_memory",
    "kv_bytes_per_token",
    "parse_tool_calls_fallback",
]

Channel = Literal["prose", "thinking", "tool"]

DEFAULT_OPEN_THINK: tuple[str, ...] = ("<think>", "<thinking>")
DEFAULT_CLOSE_THINK: tuple[str, ...] = ("</think>", "</thinking>")


# ---------------------------------------------------------------------------
# Span splitting
# ---------------------------------------------------------------------------


@dataclass
class SpanSplitter:
    """Route streamed text into prose / thinking / tool channels.

    The hard part is that sentinels arrive split across token boundaries: a
    model emitting ``</think>`` may stream ``"</th"`` then ``"ink>"``. Emitting
    the first fragment as prose would leak markup into the user's transcript
    and leave the scanner permanently in the wrong mode. So every emission
    holds back a tail of ``max(len(sentinel)) - 1`` characters, which is the
    longest possible incomplete prefix of any sentinel. :meth:`flush` releases
    the tail once generation ends.

    ``starts_in_thinking`` covers reasoning models whose chat template emits
    the opening ``<think>`` as part of the generation prompt — the stream then
    begins *inside* a reasoning span and only ever shows the closing tag.

    Usage::

        s = SpanSplitter(tool_open=("<tool_call>",), tool_close=("</tool_call>",))
        for chunk in stream:
            for channel, text in s.feed(chunk):
                ...
        for channel, text in s.flush():
            ...
        raw_tool_text = s.tool_text
    """

    open_think: tuple[str, ...] = DEFAULT_OPEN_THINK
    close_think: tuple[str, ...] = DEFAULT_CLOSE_THINK
    tool_open: tuple[str, ...] = ()
    tool_close: tuple[str, ...] = ()
    starts_in_thinking: bool = False

    mode: Channel = field(init=False, default="prose")
    _buf: str = field(init=False, default="")
    _tool_chunks: list[str] = field(init=False, default_factory=list)

    def __post_init__(self) -> None:
        self.mode = "thinking" if self.starts_in_thinking else "prose"

    # -- public ------------------------------------------------------------

    @property
    def tool_text(self) -> str:
        """Everything routed to the tool channel so far, concatenated."""
        return "".join(self._tool_chunks)

    @property
    def hold(self) -> int:
        """Characters held back from each emission."""
        longest = max((len(s) for s in self._all_sentinels()), default=1)
        return max(longest - 1, 0)

    def feed(self, chunk: str) -> list[tuple[Channel, str]]:
        """Consume a chunk, returning ``(channel, text)`` pairs to emit."""
        if not chunk:
            return []
        self._buf += chunk
        return self._drain(final=False)

    def flush(self) -> list[tuple[Channel, str]]:
        """Release the held-back tail. Call once, after the stream ends."""
        return self._drain(final=True)

    # -- internals ---------------------------------------------------------

    def _all_sentinels(self) -> tuple[str, ...]:
        return tuple(
            s
            for s in (*self.open_think, *self.close_think, *self.tool_open, *self.tool_close)
            if s
        )

    def _transitions(self) -> tuple[tuple[str, Channel], ...]:
        """Sentinels that can fire in the current mode, with their target."""
        if self.mode == "prose":
            return (
                *((s, "thinking") for s in self.open_think if s),
                *((s, "tool") for s in self.tool_open if s),
            )
        if self.mode == "thinking":
            return tuple((s, "prose") for s in self.close_think if s)
        return tuple((s, "prose") for s in self.tool_close if s)

    def _emit(self, out: list[tuple[Channel, str]], text: str) -> None:
        if not text:
            return
        if self.mode == "tool":
            self._tool_chunks.append(text)
        out.append((self.mode, text))

    def _drain(self, *, final: bool) -> list[tuple[Channel, str]]:
        out: list[tuple[Channel, str]] = []
        while True:
            transitions = self._transitions()
            hit_at = -1
            hit_len = 0
            target: Channel = "prose"
            for sentinel, dest in transitions:
                idx = self._buf.find(sentinel)
                if idx == -1:
                    continue
                # Earliest match wins; on a tie prefer the longest sentinel so
                # "<thinking>" is not mistaken for "<think>" + "ing>".
                if hit_at == -1 or idx < hit_at or (idx == hit_at and len(sentinel) > hit_len):
                    hit_at, hit_len, target = idx, len(sentinel), dest

            if hit_at == -1:
                break

            self._emit(out, self._buf[:hit_at])
            self._buf = self._buf[hit_at + hit_len :]
            self.mode = target

        if final:
            self._emit(out, self._buf)
            self._buf = ""
        else:
            keep = self.hold
            if len(self._buf) > keep:
                cut = len(self._buf) - keep
                self._emit(out, self._buf[:cut])
                self._buf = self._buf[cut:]
        return out


# ---------------------------------------------------------------------------
# Tool-parser selection
# ---------------------------------------------------------------------------

# Maps a model's ``config.json`` architecture / model_type to a module under
# ``mlx_lm.tool_parsers``. Pinned here rather than reaching into mlx-lm's own
# dispatch so an upstream refactor is a one-line fix in a tested table instead
# of a silent "model emits prose where tool calls belong" regression.
#
# Keys are matched case-insensitively as substrings of the architecture and of
# ``model_type``, longest key first.
_TOOL_PARSER_BY_ARCH: dict[str, str] = {
    "qwen3_moe": "qwen3_coder",
    "qwen3": "qwen3_coder",
    "qwen2": "json_tools",
    "gemma3": "gemma4",
    "gemma4": "gemma4",
    "glm4": "glm47",
    "kimi_k3": "kimi_k3",
    "kimi": "kimi_k2",
    "minimax": "minimax_m2",
    "minicpm": "minicpm5",
    "longcat": "longcat",
    "laguna": "laguna",
    "mistral": "mistral",
    "mixtral": "mistral",
    "llama": "pythonic",
}

# Chat-template fingerprints → parser, used when the architecture table misses.
# Order matters: the first matching marker wins.
_TOOL_PARSER_BY_TEMPLATE: tuple[tuple[str, str], ...] = (
    ("[TOOL_CALLS]", "mistral"),
    ("<|python_tag|>", "pythonic"),
    ("<tool_call>", "json_tools"),
    ("<|tool_calls_section_begin|>", "kimi_k2"),
    ("functools[", "function_gemma"),
)

# Open/close sentinel pairs we know how to detect in a chat template. The
# provider feeds the matching pair to :class:`SpanSplitter` so tool-call markup
# is never streamed to the user as prose.
TOOL_SENTINELS: tuple[tuple[str, str], ...] = (
    ("<tool_call>", "</tool_call>"),
    ("[TOOL_CALLS]", "[/TOOL_CALLS]"),
    ("<|tool_calls_section_begin|>", "<|tool_calls_section_end|>"),
    ("<|python_tag|>", "<|eom_id|>"),
    ("<tool_calls>", "</tool_calls>"),
    ("functools[", "]"),
)


def select_tool_parser_name(
    *,
    architectures: list[str] | None = None,
    model_type: str = "",
    repo_id: str = "",
    chat_template: str = "",
) -> str | None:
    """Best-guess ``mlx_lm.tool_parsers`` module name, or ``None``.

    Architecture table first (most reliable), then chat-template sniffing,
    then give up. ``None`` means the provider should fall back to
    :func:`parse_tool_calls_fallback`, and that the model should not be
    allowed to become ``default_model`` — see ws1 §S1-8.
    """
    haystacks = [
        *(a.lower() for a in (architectures or [])),
        (model_type or "").lower(),
        (repo_id or "").lower(),
    ]
    for key in sorted(_TOOL_PARSER_BY_ARCH, key=len, reverse=True):
        if any(key in h for h in haystacks if h):
            return _TOOL_PARSER_BY_ARCH[key]

    tpl = chat_template or ""
    for marker, parser in _TOOL_PARSER_BY_TEMPLATE:
        if marker in tpl:
            return parser
    return None


def sniff_tool_sentinels(chat_template: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """``(open_sentinels, close_sentinels)`` present in ``chat_template``.

    Returns empty tuples when none are recognized, in which case the splitter
    simply never enters the tool channel and the provider relies on
    :func:`parse_tool_calls_fallback` over the whole reply.
    """
    tpl = chat_template or ""
    opens: list[str] = []
    closes: list[str] = []
    for open_s, close_s in TOOL_SENTINELS:
        if open_s in tpl:
            opens.append(open_s)
            if close_s in tpl:
                closes.append(close_s)
    return tuple(opens), tuple(closes)


def template_opens_thinking(
    rendered_generation_prompt: str,
    *,
    open_think: tuple[str, ...] = DEFAULT_OPEN_THINK,
    close_think: tuple[str, ...] = DEFAULT_CLOSE_THINK,
) -> bool:
    """True when the generation prompt leaves the model inside a think span.

    Some reasoning templates emit the opening ``<think>`` themselves, so the
    stream only ever contains the closing tag. Detected by checking whether the
    rendered prompt's trailing text contains an unclosed opener. Pass the
    result as ``SpanSplitter(starts_in_thinking=...)``.
    """
    tail = rendered_generation_prompt or ""
    last_open = max((tail.rfind(s) for s in open_think if s), default=-1)
    if last_open == -1:
        return False
    last_close = max((tail.rfind(s) for s in close_think if s), default=-1)
    return last_close < last_open


_JSON_OBJ_RE = re.compile(r"\{.*?\}(?=\s*(?:,|\]|$))", re.DOTALL)


def parse_tool_calls_fallback(raw: str, tools: list[dict[str, Any]] | None = None) -> list[dict]:
    """Last-resort tool-call parser for models with no mlx-lm parser.

    Accepts a bare JSON object, a JSON array of objects, or a loose run of
    objects, and normalizes to ``[{"name": str, "arguments": dict}]``. Known
    tool names (when ``tools`` is given) are used to reject objects that are
    plainly not tool calls, so a model that merely *talks* about JSON does not
    get its prose turned into a phantom call.

    Returns ``[]`` rather than raising — a failed parse must degrade to "the
    model said something", never kill the turn.
    """
    text = (raw or "").strip()
    if not text:
        return []

    known: set[str] = set()
    for t in tools or []:
        fn = t.get("function") if isinstance(t, dict) else None
        name = (fn or {}).get("name") if isinstance(fn, dict) else t.get("name")
        if isinstance(name, str) and name:
            known.add(name)

    candidates: list[Any] = []
    try:
        parsed = json.loads(text)
        candidates = parsed if isinstance(parsed, list) else [parsed]
    except (json.JSONDecodeError, TypeError):
        for match in _JSON_OBJ_RE.finditer(text):
            try:
                candidates.append(json.loads(match.group(0)))
            except json.JSONDecodeError:
                continue

    out: list[dict] = []
    for cand in candidates:
        if not isinstance(cand, dict):
            continue
        name = cand.get("name") or cand.get("tool") or cand.get("function")
        if isinstance(name, dict):  # {"function": {"name": ..., "arguments": ...}}
            inner = name
            name = inner.get("name")
            args = inner.get("arguments")
        else:
            args = cand.get("arguments")
            if args is None:
                args = cand.get("parameters")
            if args is None:
                args = cand.get("args")
        if not isinstance(name, str) or not name:
            continue
        if known and name not in known:
            continue
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        if not isinstance(args, dict):
            args = {}
        out.append({"name": name, "arguments": args})
    return out


# ---------------------------------------------------------------------------
# Memory sizing
# ---------------------------------------------------------------------------

# Headroom over weights + KV for activations, the tokenizer, framework
# overhead and the rest of the OS. Deliberately generous: the failure mode we
# are avoiding (swap thrash / Metal OOM mid-turn) is far worse than refusing a
# borderline model.
_SLACK_GB = 1.5
_BYTES_PER_GB = 1_000_000_000


def _kv_bytes_per_token(cfg: dict[str, Any]) -> int:
    """Per-token KV-cache cost in bytes, from a HF ``config.json``.

    ``layers * kv_heads * head_dim * 2 (K and V) * 2 (fp16)``. Falls back
    through the usual aliases, including the ``text_config`` nesting that
    vision-language configs use.
    """
    node = cfg
    if "num_hidden_layers" not in node and isinstance(cfg.get("text_config"), dict):
        node = cfg["text_config"]

    layers = int(node.get("num_hidden_layers") or node.get("n_layer") or 0)
    heads = int(node.get("num_attention_heads") or node.get("n_head") or 0)
    kv_heads = int(node.get("num_key_value_heads") or heads or 0)
    hidden = int(node.get("hidden_size") or node.get("n_embd") or 0)
    head_dim = int(node.get("head_dim") or 0)
    if not head_dim and heads and hidden:
        head_dim = hidden // heads

    if not (layers and kv_heads and head_dim):
        return 0
    return layers * kv_heads * head_dim * 2 * 2


def kv_bytes_per_token(cfg: dict[str, Any]) -> int:
    """Public alias — used by the pre-download size estimate in
    :mod:`nexus.local_llm.mlx_hf_search`, which has only the remote
    ``config.json`` and no local snapshot to measure."""
    return _kv_bytes_per_token(cfg)


def estimate_resident_gb(
    snapshot_dir: Path | str,
    *,
    context_tokens: int = 8192,
    config: dict[str, Any] | None = None,
) -> float:
    """Approximate resident memory for a loaded MLX snapshot, in GB.

    Weights are measured from the on-disk ``*.safetensors`` sizes (exact for a
    quantized MLX repo), KV is derived from ``config.json``, plus
    :data:`_SLACK_GB`. Returns ``0.0`` when the directory has no weights, so
    callers can distinguish "doesn't fit" from "nothing there".
    """
    directory = Path(snapshot_dir)
    weight_bytes = 0
    if directory.is_dir():
        for shard in directory.glob("*.safetensors"):
            try:
                weight_bytes += shard.stat().st_size
            except OSError:
                continue
    if weight_bytes == 0:
        return 0.0

    cfg = config
    if cfg is None:
        cfg_path = directory / "config.json"
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cfg = {}

    kv_bytes = _kv_bytes_per_token(cfg) * max(int(context_tokens or 0), 0)
    total_gb = (weight_bytes + kv_bytes) / _BYTES_PER_GB + _SLACK_GB
    return round(total_gb, 2)


def fits_in_unified_memory(need_gb: float, hw: dict[str, Any]) -> tuple[bool, str]:
    """Whether ``need_gb`` fits, with a user-facing explanation either way.

    ``hw`` is a :func:`nexus.local_llm.hardware.probe` result. On Apple Silicon
    the budget is unified memory (``vram_gb``, which the probe sets equal to
    total RAM); elsewhere we have no device budget to reason about, so this
    reports unknown-but-allowed rather than blocking.
    """
    if need_gb <= 0:
        return False, "no model weights found in that snapshot"

    if not hw.get("is_apple_silicon"):
        return True, (
            f"needs about {need_gb:.1f} GB; this host is not Apple Silicon, "
            "so MLX cannot be sized here"
        )

    total = float(hw.get("vram_gb") or hw.get("ram_gb") or 0.0)
    if total <= 0:
        return True, f"needs about {need_gb:.1f} GB; could not read total memory"

    # Leave a quarter of unified memory for the OS, the UI and everything else
    # the machine is doing. Past that point macOS starts compressing/swapping
    # and generation speed collapses long before it actually fails.
    budget = total * 0.75
    if need_gb <= budget:
        return True, f"needs about {need_gb:.1f} GB of {total:.0f} GB unified memory"
    return False, (
        f"needs about {need_gb:.1f} GB but only ~{budget:.1f} GB of "
        f"{total:.0f} GB unified memory is safely available — "
        "pick a smaller quant or lower the context window"
    )
