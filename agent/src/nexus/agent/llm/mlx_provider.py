"""In-process MLX provider — local inference on Apple Silicon.

Implements :class:`~nexus.agent.llm.types.LLMProvider` on top of ``mlx_lm``
running inside this process, rather than a supervised ``mlx_lm.server``
subprocess. The payoff is the prefix KV cache (``LRUPromptCache``), which is
only reachable in-process and which makes a long single-thread conversation
cheap: a tool loop's message list is strictly append-only, so iterations 2..N
of a turn reuse the whole prefill.

Three things this provider does that no other Nexus provider has to:

* **Split its own output channels.** OpenAI-compat gives us
  ``delta.reasoning`` and ``delta.tool_calls`` pre-separated; MLX gives one
  flat text stream. :class:`~nexus.agent.llm.mlx_support.SpanSplitter` routes
  it into prose / thinking / tool, holding back a tail so a sentinel split
  across token boundaries never leaks as prose.
* **Parse tool calls itself**, via a per-architecture ``mlx_lm.tool_parsers``
  module chosen at load time, with a generic JSON fallback.
* **Key its cache off a ContextVar.** ``chat_stream`` has no ``extra_payload``
  parameter and ``LoomProviderAdapter`` passes only ``tools``/``model``/
  ``max_tokens``, so there is no per-call channel to say *which bot* this
  generation belongs to. ``context.CURRENT_SESSION_ID`` is set by
  ``ChatTurnRunner`` and visible here because it is the same task.

Serialization, residency and OOM handling live in
:mod:`nexus.agent.llm.mlx_runtime`; this module is the protocol translation.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from loom.types import ToolSpec, Usage

from .mlx_runtime import MlxNotAvailable, MlxRuntime
from .mlx_support import (
    DEFAULT_CLOSE_THINK,
    DEFAULT_OPEN_THINK,
    SpanSplitter,
    parse_tool_calls_fallback,
)
from .openai import _encode_msg, _encode_tool
from .types import (
    ChatMessage,
    ChatResponse,
    LLMProvider,
    LLMTransportError,
    StopReason,
    StreamEvent,
    ToolCall,
)

log = logging.getLogger(__name__)

__all__ = ["MlxProvider"]

# Matches llama.cpp's default and the `[agent].default_max_output_tokens`
# fallback behaviour: generate something sane when nobody said otherwise.
_DEFAULT_MAX_TOKENS = 4096


class MlxProvider(LLMProvider):
    """Local MLX inference.

    ``model_path`` is an absolute path to a Hugging Face snapshot directory
    (what ``mlx_manager`` writes into ``providers.mlx-<slug>.model_path``) or
    any string ``mlx_lm.load`` accepts. The ``model`` argument that callers
    pass to :meth:`chat` / :meth:`chat_stream` is **ignored for routing** —
    one provider instance serves exactly one local model, the same way a
    ``local-*`` llama-server provider does.
    """

    #: Set ``True`` so ``_builder`` can place the volatile prompt zone in a
    #: trailing SYSTEM message instead of inlining it at the head, which is
    #: what makes cross-turn prefix reuse possible. Gated per-provider because
    #: the Anthropic encoder keeps only the *last* SYSTEM message, so moving
    #: the zone unconditionally would let it replace the whole prompt there.
    #: Until ``_builder`` honours this flag it is inert and harmless.
    prefers_trailing_volatile_zone: bool = True

    def __init__(
        self,
        *,
        model_path: str,
        model: str = "",
        temperature: float = 0.0,
        max_tokens: int | None = None,
        runtime: MlxRuntime | None = None,
    ) -> None:
        self.model_path = model_path
        self.model = model
        self.temperature = float(temperature or 0.0)
        self.default_max_tokens = int(max_tokens or 0) or _DEFAULT_MAX_TOKENS
        self._runtime = runtime or MlxRuntime.get()

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _cache_key() -> str | None:
        """Session id for the prefix cache, or ``None`` outside a turn."""
        try:
            from ..context import CURRENT_SESSION_ID

            value = CURRENT_SESSION_ID.get()
        except Exception:  # pragma: no cover - ContextVar always exists
            return None
        return value or None

    @staticmethod
    def _strip_markers(messages: list[ChatMessage]) -> list[dict[str, Any]]:
        """OpenAI-shaped dicts with the cache breakpoint marker removed.

        MLX has no server-side cache-control, so the marker has no meaning
        here — and it must never reach a tokenizer. ``_encode_msg`` is
        imported from the OpenAI provider rather than reimplemented because
        chat templates consume exactly that shape, which also gets
        ``tool_calls`` / ``tool_call_id`` / ``reasoning_content`` round-tripping
        for free.
        """
        from ..prompt_builder import strip_cache_marker

        encoded: list[dict[str, Any]] = []
        for m in messages:
            row = _encode_msg(m)
            content = row.get("content")
            if isinstance(content, str):
                row["content"] = strip_cache_marker(content)
            elif isinstance(content, list):
                cleaned: list[Any] = []
                for part in content:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        part = {**part, "text": strip_cache_marker(part["text"])}
                    cleaned.append(part)
                row["content"] = cleaned
            encoded.append(row)
        return encoded

    def _build_prompt_tokens(
        self, loaded: Any, messages: list[ChatMessage], tools: list[ToolSpec] | None
    ) -> list[int]:
        """Render the chat template to token ids.

        A template with no ``tools`` variable raises ``TemplateError``; we
        catch it once, mark the model tool-less, and retry without tools so the
        turn still produces *something* rather than dying.
        """
        chat_messages = self._strip_markers(messages)
        chat_messages = _tool_args_as_dicts(chat_messages)
        encoded_tools = [_encode_tool(t) for t in (tools or [])] or None

        def render(with_tools: bool) -> Any:
            kwargs: dict[str, Any] = {"add_generation_prompt": True, "tokenize": True}
            if with_tools and encoded_tools:
                kwargs["tools"] = encoded_tools
            return loaded.tokenizer.apply_chat_template(chat_messages, **kwargs)

        try:
            tokens = render(bool(encoded_tools) and loaded.supports_tools)
        except Exception as exc:
            if encoded_tools and loaded.supports_tools:
                log.warning(
                    "[mlx] %s chat template rejected tools (%s); "
                    "retrying without them and marking the model tool-less",
                    self.model_path,
                    exc,
                )
                loaded.supports_tools = False
                try:
                    tokens = render(False)
                except Exception as exc2:
                    raise LLMTransportError(
                        f"mlx: chat template failed for {self.model_path}: {exc2}"
                    ) from exc2
            else:
                raise LLMTransportError(
                    f"mlx: chat template failed for {self.model_path}: {exc}"
                ) from exc

        return list(tokens)

    def _parse_tool_calls(self, loaded: Any, raw: str, tools: list[ToolSpec] | None) -> list[dict]:
        """Tool calls out of the buffered tool-channel text.

        mlx-lm's parsers are whole-span parsers, which is why this runs once at
        the end rather than incrementally.
        """
        if not raw.strip():
            return []
        encoded_tools = [_encode_tool(t) for t in (tools or [])]
        parser = getattr(loaded, "tool_parser", None)
        if callable(parser):
            try:
                parsed = parser(raw, encoded_tools)
                normalized = _normalize_tool_calls(parsed)
                if normalized:
                    return normalized
                log.debug("[mlx] parser %s returned nothing usable", loaded.tool_parser_name)
            except Exception:
                log.warning(
                    "[mlx] tool parser %s raised; falling back to generic JSON",
                    getattr(loaded, "tool_parser_name", "?"),
                    exc_info=True,
                )
        return parse_tool_calls_fallback(raw, encoded_tools)

    # -- LLMProvider -------------------------------------------------------

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        try:
            loaded = await self._runtime.ensure_loaded(self.model_path)
        except MlxNotAvailable as exc:
            raise LLMTransportError(f"mlx unavailable: {exc}") from exc

        prompt_tokens = self._build_prompt_tokens(loaded, messages, tools)

        splitter = SpanSplitter(
            open_think=DEFAULT_OPEN_THINK,
            close_think=DEFAULT_CLOSE_THINK,
            tool_open=loaded.tool_open,
            tool_close=loaded.tool_close,
            starts_in_thinking=loaded.starts_in_thinking,
        )

        prose: list[str] = []
        reasoning: list[str] = []
        end_meta: dict[str, Any] = {}

        stream = self._runtime.generate_stream(
            model_path=self.model_path,
            prompt_tokens=prompt_tokens,
            max_tokens=int(max_tokens or 0) or self.default_max_tokens,
            cache_key=self._cache_key(),
            sampler=self._sampler(),
        )

        async for kind, payload in stream:
            if kind == "end":
                end_meta = payload or {}
                break
            for channel, text in splitter.feed(payload):
                if channel == "prose":
                    prose.append(text)
                    yield {"type": "delta", "text": text}
                elif channel == "thinking":
                    reasoning.append(text)
                    yield {"type": "thinking_delta", "text": text}
                # tool text is buffered in the splitter, never emitted here

        for channel, text in splitter.flush():
            if channel == "prose":
                prose.append(text)
                yield {"type": "delta", "text": text}
            elif channel == "thinking":
                reasoning.append(text)
                yield {"type": "thinking_delta", "text": text}

        parsed = self._parse_tool_calls(loaded, splitter.tool_text, tools)

        tool_calls: list[dict[str, Any]] = []
        for i, tc in enumerate(parsed):
            tc_id = tc.get("id") or f"mlxtc_{i}"
            name = tc.get("name") or ""
            args = tc.get("arguments") if isinstance(tc.get("arguments"), dict) else {}
            yield {"type": "tool_call_start", "id": tc_id, "name": name}
            yield {
                "type": "tool_call_delta",
                "id": tc_id,
                "args_delta": _json_dumps(args),
            }
            yield {"type": "tool_call_end", "id": tc_id}
            tool_calls.append({"id": tc_id, "name": name, "arguments": args})

        usage = Usage(
            input_tokens=int(end_meta.get("prompt_tokens") or len(prompt_tokens)),
            output_tokens=int(end_meta.get("generation_tokens") or 0),
        )

        # Nexus's StopReason values, not OpenAI's: _loom_bridge maps on this
        # enum, so "tool_calls" here would be silently unrecognized.
        if tool_calls:
            finish = StopReason.TOOL_USE.value
        elif end_meta.get("finish_reason") == "length":
            finish = StopReason.LENGTH.value
        else:
            finish = StopReason.STOP.value

        event: StreamEvent = {
            "type": "finish",
            "finish_reason": finish,
            "content": "".join(prose),
            "tool_calls": tool_calls,
            "usage": usage.model_dump(),
            "reasoning_content": "".join(reasoning) or None,
        }
        if end_meta.get("finish_reason") == "cancelled":
            event["abort_reason"] = "cancelled"
        yield event

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        extra_payload: dict[str, Any] | None = None,
    ) -> ChatResponse:
        """Non-streaming path, built on :meth:`chat_stream`.

        MLX is a token generator; there is no separate one-shot API worth
        maintaining, so this drains the stream. ``extra_payload`` has no
        meaning for a local runtime and is ignored.
        """
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        finish: dict[str, Any] = {}

        async for event in self.chat_stream(
            messages, tools=tools, model=model, max_tokens=max_tokens
        ):
            etype = event.get("type")
            if etype == "delta":
                content_parts.append(event.get("text") or "")
            elif etype == "thinking_delta":
                reasoning_parts.append(event.get("text") or "")
            elif etype == "finish":
                finish = event

        tool_calls = [
            ToolCall(
                id=tc.get("id") or "",
                name=tc.get("name") or "",
                arguments=tc.get("arguments") if isinstance(tc.get("arguments"), dict) else {},
            )
            for tc in finish.get("tool_calls") or []
        ]

        raw_usage = finish.get("usage") or {}
        usage = Usage(
            input_tokens=int(raw_usage.get("input_tokens") or 0),
            output_tokens=int(raw_usage.get("output_tokens") or 0),
        )

        try:
            stop_reason = StopReason(finish.get("finish_reason") or "stop")
        except ValueError:
            stop_reason = StopReason.UNKNOWN

        return ChatResponse(
            content="".join(content_parts) or None,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            usage=usage,
            reasoning_content="".join(reasoning_parts) or None,
        )

    async def aclose(self) -> None:
        """Do **not** unload here.

        ``aclose`` is called whenever the registry is rebuilt (a settings save,
        a model activation), which happens far more often than a user actually
        wants multi-GB weights evicted. Residency is the runtime's business,
        governed by its idle timer and by switching models.
        """
        return

    # -- sampling ----------------------------------------------------------

    def _sampler(self) -> Any:
        """An mlx-lm sampler for the configured temperature, or ``None``.

        ``None`` means "let mlx-lm decide", which is the right behaviour at
        temperature 0 (the default everywhere in Nexus) since greedy decoding
        is already mlx-lm's default.
        """
        if self.temperature <= 0:
            return None
        try:
            from mlx_lm.sample_utils import make_sampler  # type: ignore[import-not-found]

            return make_sampler(temp=self.temperature)
        except Exception:
            log.debug("[mlx] make_sampler unavailable; using defaults", exc_info=True)
            return None


def _json_dumps(value: Any) -> str:
    import json

    try:
        return json.dumps(value)
    except (TypeError, ValueError):
        return "{}"


def _tool_args_as_dicts(chat_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Render-shape fixup: tool-call ``arguments`` as objects, not strings.

    ``_encode_msg`` emits OpenAI wire shape, where ``function.arguments`` is a
    JSON *string*. Several chat templates (GLM-4.x, Gemma, and others) instead
    iterate the arguments object directly — ``arguments.items()`` — and blow
    up on a string ('str object' has no attribute 'items'). Parsing to a dict
    here is universally safe: ``| tojson`` over a dict renders the same JSON
    the string already carried, and ``.items()``-style templates get what they
    expect. Only the render path is touched; the OpenAI wire format is
    unaffected.
    """
    for m in chat_messages:
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function") if isinstance(tc, dict) else None
            if not isinstance(fn, dict):
                continue
            args = fn.get("arguments")
            if isinstance(args, str):
                try:
                    fn["arguments"] = json.loads(args)
                except json.JSONDecodeError:
                    fn["arguments"] = {}
            elif args is None:
                fn["arguments"] = {}
            elif not isinstance(args, dict):
                fn["arguments"] = {}
    return chat_messages


def _normalize_tool_calls(parsed: Any) -> list[dict]:
    """Coerce whatever a tool parser returned into our shape.

    mlx-lm parsers are not uniform — some return OpenAI-style
    ``{"function": {"name", "arguments"}}`` wrappers, some return flat dicts,
    some return objects. Anything unrecognizable is dropped rather than
    guessed at.
    """
    if not parsed:
        return []
    if isinstance(parsed, dict):
        parsed = [parsed]
    if not isinstance(parsed, (list, tuple)):
        return []

    out: list[dict] = []
    for item in parsed:
        row: dict[str, Any] | None = None
        if isinstance(item, dict):
            row = item
        else:
            name = getattr(item, "name", None)
            if name is not None:
                row = {
                    "id": getattr(item, "id", None),
                    "name": name,
                    "arguments": getattr(item, "arguments", None),
                }
        if not row:
            continue

        fn = row.get("function")
        if isinstance(fn, dict):
            name = fn.get("name")
            args = fn.get("arguments")
            tc_id = row.get("id")
        else:
            name = row.get("name") or row.get("tool")
            args = row.get("arguments")
            if args is None:
                args = row.get("parameters")
            if args is None:
                args = row.get("args")
            tc_id = row.get("id")

        if not isinstance(name, str) or not name:
            continue
        if isinstance(args, str):
            import json

            try:
                args = json.loads(args)
            except (json.JSONDecodeError, TypeError):
                args = {}
        if not isinstance(args, dict):
            args = {}
        out.append({"id": tc_id, "name": name, "arguments": args})
    return out
