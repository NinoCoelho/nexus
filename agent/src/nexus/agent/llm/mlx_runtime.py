"""Process-global MLX runtime: model residency, serialization, prefix cache.

Why a singleton rather than state on the provider: ``registry.build_registry``
is rebuilt on **every** config change (``routes/local_llm.py`` calls
``_rebuild_registry`` from four places), so per-provider state would leak a
worker thread and reload multi-GB weights each time.

Three invariants, in order of importance:

1. **One generation at a time.** MLX generation is synchronous, GIL-bound and
   shares one Metal device. A single ``asyncio.Lock`` serializes it — the same
   shape ``providers/probe.py`` uses. A second turn *queues*; it never preempts
   (``stream_generate`` has no safe mid-step abort, and preempting throws away
   the prefill) and is never rejected (the only concurrent callers are other
   bots and background heartbeat turns — a "GPU is busy" error there is just a
   lost turn).
2. **Never block the event loop.** The worker thread runs the sync generator
   and hands coalesced chunks over via ``loop.call_soon_threadsafe``. Awaiting
   the whole generation in an executor would be one ``await`` for the entire
   turn and streaming would die.
3. **One model resident.** Unified memory means a loaded model is *resident*;
   loading a second while the first is alive is how you OOM a Mac. Switching
   models unloads first, and an idle timer unloads after inactivity.

All mlx imports are lazy (inside methods) so this module is importable — and
most of it testable — on any OS.
"""

from __future__ import annotations

import asyncio
import logging
import platform
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .types import LLMTransportError

log = logging.getLogger(__name__)

__all__ = ["MlxRuntime", "MlxNotAvailable", "LoadedModel", "get_runtime", "reset_runtime"]

# Coalescing thresholds for the token bridge. The cost we are avoiding is not
# the thread hop (~60 wakeups/s is nothing) but what each delta triggers
# downstream: one SSE frame + TurnAccumulator.process_event + a pub-sub
# publish, per token.
_COALESCE_SECONDS = 0.004
_COALESCE_CHARS = 24

# Default wait for the generation lock before giving up. Long, because the
# thing we are waiting for is another bot's turn finishing, and the adapter's
# idle watchdog deliberately does not bound the pre-first-event window.
DEFAULT_QUEUE_TIMEOUT = 300.0

# Silence budget inside a generation. Real generations emit (coalesced)
# output every few seconds even while a reasoning model is thinking, and
# prefill on big prompts is covered by scaling below — so silence beyond
# this means the worker wedged inside mlx (Metal-land hangs produce no
# exception and no token), and the turn would otherwise hang forever.
DEFAULT_STALL_TIMEOUT = 120.0
# Prefill floor: assume at least 50 prompt tokens/second on Apple Silicon.
# A 26k-token history then buys ~520s of prefill silence before the stall
# trips, while a normal turn is rescued after 2 minutes.
_STALL_PREFILL_TOK_PER_SEC = 50.0

# Every MLX op — model load, generation, prompt-cache reads/writes — runs on
# this single dedicated thread. MLX arrays are bound to the thread/stream that
# created them: let the default asyncio pool round-robin load onto thread A
# and generation onto thread B and touching the persisted KV cache dies with
# "There is no Stream(gpu, 1) in current thread". One thread, forever, makes
# the whole class impossible. It is idle-exit-safe: a wedged worker may keep
# it busy, and the stall timeout cancels the *consumer*; the daemon process
# owns the executor's lifetime.
_MLX_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mlx")

# Unload a resident model after this much inactivity.
DEFAULT_IDLE_UNLOAD_SECONDS = 900.0

_OOM_MARKERS = ("out of memory", "insufficient memory", "mtlbuffer", "failed to allocate")


class MlxNotAvailable(RuntimeError):
    """Raised when mlx / mlx-lm is not importable on this host."""


@dataclass
class LoadedModel:
    """A resident model plus everything we derived from it at load time."""

    model_path: str
    model: Any
    tokenizer: Any
    chat_template: str = ""
    tool_parser_name: str | None = None
    tool_parser: Any = None
    tool_open: tuple[str, ...] = ()
    tool_close: tuple[str, ...] = ()
    starts_in_thinking: bool = False
    supports_tools: bool = True
    context_window: int = 0
    info: Any = None  # MlxModelInfo, when the path was a managed snapshot
    loaded_at: float = field(default_factory=time.monotonic)


def _is_oom(exc: BaseException) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in text for marker in _OOM_MARKERS)


class MlxRuntime:
    """Owns the resident model, the generation lock and the prefix caches."""

    _instance: MlxRuntime | None = None

    # -- singleton ---------------------------------------------------------

    @classmethod
    def get(cls) -> MlxRuntime:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Drop the singleton. Tests only."""
        cls._instance = None

    def __init__(
        self,
        *,
        idle_unload_seconds: float = DEFAULT_IDLE_UNLOAD_SECONDS,
        cache_max_entries: int = 4,
    ) -> None:
        self._gen_lock = asyncio.Lock()
        self._load_lock = asyncio.Lock()
        self._loaded: LoadedModel | None = None
        self._caches: dict[str, Any] = {}
        self._last_used: float = 0.0
        self._idle_task: asyncio.Task[None] | None = None
        self._idle_unload_seconds = float(idle_unload_seconds)
        self._cache_max_entries = int(cache_max_entries)
        self._queue_depth = 0

    # -- availability ------------------------------------------------------

    @staticmethod
    def available() -> tuple[bool, str]:
        """``(importable, reason)`` — safe to call anywhere, never raises."""
        if platform.system() != "Darwin":
            return False, "MLX requires macOS on Apple Silicon"
        if platform.machine() != "arm64":
            return False, f"MLX requires Apple Silicon (this is {platform.machine()})"
        try:
            import mlx.core  # noqa: F401
            import mlx_lm  # noqa: F401
        except ImportError as exc:
            return False, f"mlx extra not installed ({exc}); run `uv sync --extra mlx`"
        return True, "ready"

    def _require_available(self) -> None:
        ok, reason = self.available()
        if not ok:
            raise MlxNotAvailable(reason)

    # -- status ------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        loaded = self._loaded
        out: dict[str, Any] = {
            "loaded": loaded is not None,
            "model_path": loaded.model_path if loaded else None,
            "context_window": loaded.context_window if loaded else 0,
            "supports_tools": loaded.supports_tools if loaded else None,
            "tool_parser": loaded.tool_parser_name if loaded else None,
            "resident_seconds": round(time.monotonic() - loaded.loaded_at, 1) if loaded else 0,
            "queue_depth": self._queue_depth,
            "idle_unload_seconds": self._idle_unload_seconds,
            "caches": {},
        }
        for sid, bundle in self._caches.items():
            try:
                out["caches"][sid] = {
                    "bytes": int(getattr(bundle, "nbytes", 0) or 0),
                    "entries": len(bundle),
                    "by_type": bundle.stats_by_type(),
                }
            except Exception:  # pragma: no cover - diagnostics only
                out["caches"][sid] = {"error": "unavailable"}
        return out

    # -- load / unload -----------------------------------------------------

    async def ensure_loaded(self, model_path: str) -> LoadedModel:
        """Load ``model_path``, unloading any other resident model first."""
        self._require_available()
        async with self._load_lock:
            current = self._loaded
            if current is not None and current.model_path == model_path:
                return current
            if current is not None:
                log.info(
                    "[mlx] switching resident model %s -> %s (unloading first)",
                    current.model_path,
                    model_path,
                )
                await self._unload_locked()
            loop = asyncio.get_running_loop()
            loaded = await loop.run_in_executor(_MLX_EXECUTOR, self._load_blocking, model_path)
            self._loaded = loaded
            self._last_used = time.monotonic()
            log.info(
                "[mlx] loaded %s (window=%s tools=%s parser=%s)",
                model_path,
                loaded.context_window or "unknown",
                loaded.supports_tools,
                loaded.tool_parser_name or "none",
            )
            return loaded

    def _load_blocking(self, model_path: str) -> LoadedModel:
        """Synchronous load. Runs in a thread; raises on failure."""
        from mlx_lm import load  # type: ignore[import-not-found]

        from ...local_llm.mlx_snapshot import read_snapshot
        from .mlx_support import (
            sanitize_chat_template,
            select_tool_parser_name,
            sniff_tool_sentinels,
            template_opens_thinking,
        )

        try:
            model, tokenizer = load(model_path)
        except Exception as exc:
            if _is_oom(exc):
                raise LLMTransportError(
                    f"mlx: out of memory loading {model_path}; "
                    "pick a smaller quant or lower the context window",
                    status_code=507,
                ) from exc
            raise LLMTransportError(f"mlx: failed to load {model_path}: {exc}") from exc

        info = None
        directory = Path(model_path)
        if directory.is_dir():
            try:
                info = read_snapshot(directory)
            except Exception:  # pragma: no cover - metadata is best-effort
                log.warning("[mlx] could not read snapshot metadata for %s", model_path)

        chat_template = ""
        if info is not None and info.chat_template:
            chat_template = info.chat_template
        else:
            raw = getattr(tokenizer, "chat_template", "") or ""
            chat_template = raw if isinstance(raw, str) else ""

        # Repair known-broken tool examples shipped inside some chat templates
        # (e.g. doubled-brace Qwen2.5 instructions that teach the model to emit
        # invalid JSON). Applied to the tokenizer itself so every
        # apply_chat_template call renders the fixed text.
        sanitized = sanitize_chat_template(chat_template)
        if sanitized != chat_template:
            log.warning(
                "[mlx] repaired broken tool-call example in %s chat template",
                model_path,
            )
            chat_template = sanitized
            try:
                inner = getattr(tokenizer, "_tokenizer", None) or tokenizer
                inner.chat_template = sanitized
            except Exception:  # pragma: no cover - renderer quirks are not fatal
                log.warning("[mlx] could not persist sanitized chat template")

        tool_open, tool_close = sniff_tool_sentinels(chat_template)
        parser_name = select_tool_parser_name(
            architectures=list(getattr(info, "architectures", []) or []),
            model_type=getattr(info, "model_type", "") or "",
            repo_id=getattr(info, "repo_id", "") or model_path,
            chat_template=chat_template,
        )
        parser = _resolve_tool_parser(parser_name)

        starts_in_thinking = False
        try:
            rendered = tokenizer.apply_chat_template(
                [{"role": "user", "content": "x"}],
                add_generation_prompt=True,
                tokenize=False,
            )
            if isinstance(rendered, str):
                starts_in_thinking = template_opens_thinking(rendered)
        except Exception:  # pragma: no cover - template quirks are not fatal
            pass

        supports_tools = bool(getattr(info, "supports_tools", bool(tool_open)))

        return LoadedModel(
            model_path=model_path,
            model=model,
            tokenizer=tokenizer,
            chat_template=chat_template,
            tool_parser_name=parser_name,
            tool_parser=parser,
            tool_open=tool_open,
            tool_close=tool_close,
            starts_in_thinking=starts_in_thinking,
            supports_tools=supports_tools,
            context_window=int(getattr(info, "max_position_embeddings", 0) or 0),
            info=info,
        )

    async def unload(self) -> None:
        async with self._load_lock:
            await self._unload_locked()

    async def _unload_locked(self) -> None:
        """Drop the model and every cache, then release Metal buffers."""
        had = self._loaded is not None
        self._loaded = None
        self._caches.clear()
        try:
            import mlx.core as mx  # type: ignore[import-not-found]

            # mlx moved this from mx.metal.clear_cache() to mx.clear_cache().
            clear = getattr(mx, "clear_cache", None) or getattr(
                getattr(mx, "metal", None), "clear_cache", None
            )
            if callable(clear):
                clear()
        except Exception:  # pragma: no cover - best effort
            log.debug("[mlx] clear_cache unavailable", exc_info=True)
        if had:
            log.info("[mlx] unloaded resident model")

    # -- idle reaper -------------------------------------------------------

    def _touch(self) -> None:
        self._last_used = time.monotonic()
        self._start_idle_task()

    def _start_idle_task(self) -> None:
        if self._idle_unload_seconds <= 0:
            return
        if self._idle_task is not None and not self._idle_task.done():
            return
        try:
            self._idle_task = asyncio.create_task(self._idle_loop(), name="mlx-idle-unload")
        except RuntimeError:  # pragma: no cover - no running loop (tests)
            self._idle_task = None

    async def _idle_loop(self) -> None:
        """Unload once the model has gone unused. Failure must never wedge us."""
        try:
            while True:
                await asyncio.sleep(min(60.0, max(5.0, self._idle_unload_seconds / 4)))
                if self._loaded is None:
                    return
                idle = time.monotonic() - self._last_used
                if idle >= self._idle_unload_seconds:
                    log.info("[mlx] idle for %.0fs — unloading", idle)
                    try:
                        await self.unload()
                    except Exception:
                        log.warning("[mlx] idle unload failed", exc_info=True)
                    return
        except asyncio.CancelledError:  # pragma: no cover
            raise
        except Exception:  # pragma: no cover
            log.warning("[mlx] idle loop died", exc_info=True)

    # -- prefix cache ------------------------------------------------------

    def _cache_for(self, key: str | None) -> Any | None:
        """The ``LRUPromptCache`` for a session, created on first use.

        Returns ``None`` when caching is unavailable for any reason — a missing
        cache is a performance regression, never a correctness problem, so
        every failure here degrades silently.
        """
        if not key:
            return None
        bundle = self._caches.get(key)
        if bundle is not None:
            return bundle
        try:
            from mlx_lm.models.cache import LRUPromptCache  # type: ignore[import-not-found]

            bundle = LRUPromptCache(max_size=self._cache_max_entries)
        except Exception:
            log.debug("[mlx] LRUPromptCache unavailable; prefix caching disabled", exc_info=True)
            return None
        self._caches[key] = bundle
        return bundle

    def drop_cache(self, key: str) -> None:
        self._caches.pop(key, None)

    # -- generation --------------------------------------------------------

    async def generate_stream(
        self,
        *,
        model_path: str,
        prompt_tokens: list[int],
        max_tokens: int,
        cache_key: str | None = None,
        sampler: Any = None,
        queue_timeout: float = DEFAULT_QUEUE_TIMEOUT,
        stall_timeout: float | None = None,
    ) -> Any:
        """Async-iterate ``("text", str)`` chunks then one ``("end", dict)``.

        Serialized against every other generation. Cancelling the iterator
        stops the worker at its next token rather than letting it run on to
        ``max_tokens``.
        """
        self._require_available()
        loaded = await self.ensure_loaded(model_path)

        self._queue_depth += 1
        try:
            try:
                await asyncio.wait_for(self._gen_lock.acquire(), timeout=queue_timeout)
            except asyncio.TimeoutError as exc:
                raise LLMTransportError(
                    f"mlx: generation queue timed out after {queue_timeout:.0f}s "
                    "(another turn is still using the model)",
                    status_code=503,
                ) from exc
        finally:
            self._queue_depth -= 1

        try:
            async for item in self._run_locked(
                loaded=loaded,
                prompt_tokens=prompt_tokens,
                max_tokens=max_tokens,
                cache_key=cache_key,
                sampler=sampler,
                stall_timeout=stall_timeout,
            ):
                yield item
        finally:
            self._touch()
            self._gen_lock.release()

    async def _run_locked(
        self,
        *,
        loaded: LoadedModel,
        prompt_tokens: list[int],
        max_tokens: int,
        cache_key: str | None,
        sampler: Any,
        stall_timeout: float | None = None,
    ) -> Any:
        loop = asyncio.get_running_loop()
        out: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        cancel = threading.Event()

        bundle = self._cache_for(cache_key)
        cache_obj, feed_tokens = self._fetch_prefix(bundle, loaded, prompt_tokens)

        def emit(item: tuple[str, Any]) -> None:
            loop.call_soon_threadsafe(out.put_nowait, item)

        def worker() -> None:
            """Sync generation loop. Runs off-loop; must not raise out."""
            pending: list[str] = []
            last_flush = time.monotonic()
            produced = 0
            finish_reason = "stop"

            def flush(force: bool = False) -> None:
                nonlocal pending, last_flush
                if not pending:
                    return
                now = time.monotonic()
                size = sum(len(p) for p in pending)
                if force or size >= _COALESCE_CHARS or (now - last_flush) >= _COALESCE_SECONDS:
                    emit(("text", "".join(pending)))
                    pending = []
                    last_flush = now

            try:
                from mlx_lm import stream_generate  # type: ignore[import-not-found]

                kwargs: dict[str, Any] = {"max_tokens": max_tokens}
                if sampler is not None:
                    kwargs["sampler"] = sampler
                if cache_obj is not None:
                    kwargs["prompt_cache"] = cache_obj

                for resp in stream_generate(
                    loaded.model, loaded.tokenizer, feed_tokens, **kwargs
                ):
                    text = getattr(resp, "text", "") or ""
                    if text:
                        pending.append(text)
                        produced += 1
                        flush()
                    reason = getattr(resp, "finish_reason", None)
                    if reason:
                        finish_reason = str(reason)
                    if cancel.is_set():
                        finish_reason = "cancelled"
                        break
                flush(force=True)
                emit(
                    (
                        "end",
                        {
                            "finish_reason": finish_reason,
                            "prompt_tokens": len(prompt_tokens),
                            "generation_tokens": produced,
                        },
                    )
                )
            except BaseException as exc:  # noqa: BLE001 - must never escape the thread
                flush(force=True)
                emit(("error", exc))

        task = loop.run_in_executor(_MLX_EXECUTOR, worker)
        # Silence budget: scaled for prefill of big prompts (see constants),
        # overridable for tests.
        stall_seconds = stall_timeout if stall_timeout is not None else max(
            DEFAULT_STALL_TIMEOUT, len(prompt_tokens) / _STALL_PREFILL_TOK_PER_SEC
        )
        try:
            while True:
                try:
                    kind, payload = await asyncio.wait_for(out.get(), timeout=stall_seconds)
                except asyncio.TimeoutError as exc:
                    # The worker wedged without emitting (no token, no end, no
                    # exception) — cancel it and drop the resident model so the
                    # next attempt starts from clean Metal state.
                    cancel.set()
                    try:
                        await self._unload_locked()
                    except Exception:  # pragma: no cover - best effort
                        log.debug("[mlx] unload after stall skipped", exc_info=True)
                    raise LLMTransportError(
                        f"mlx: generation stalled — no output for {stall_seconds:.0f}s; "
                        "the attempt was cancelled and the model unloaded for a clean retry",
                        status_code=504,
                    ) from exc
                if kind == "text":
                    yield ("text", payload)
                    continue
                if kind == "error":
                    raise self._classify(payload)
                yield ("end", payload)
                return
        except asyncio.CancelledError:
            cancel.set()
            raise
        finally:
            cancel.set()
            # The worker only ever puts on the queue, so letting it finish is
            # bounded by one more token.
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=30.0)
            except (asyncio.TimeoutError, Exception):  # noqa: B014 - diagnostics only
                pass
            self._insert_prefix(bundle, loaded, prompt_tokens, cache_obj)

    # -- cache plumbing ----------------------------------------------------

    def _fetch_prefix(
        self, bundle: Any, loaded: LoadedModel, prompt_tokens: list[int]
    ) -> tuple[Any, Any]:
        """``(cache_object, tokens_to_feed)``.

        ``LRUPromptCache`` is content-addressed by token prefix, so there is no
        invalidation to orchestrate: after a compaction rewrites the head, the
        nearest match is simply empty and we re-prefill. Any failure degrades
        to "no cache, feed everything".
        """
        if bundle is None:
            return None, prompt_tokens
        try:
            cache, remaining = bundle.fetch_nearest_cache(loaded.model, prompt_tokens)
            if cache is None:
                return None, prompt_tokens
            reused = len(prompt_tokens) - len(remaining)
            if reused > 0:
                log.debug("[mlx] prefix cache reused %d/%d tokens", reused, len(prompt_tokens))
            return cache, remaining
        except Exception:
            log.debug("[mlx] fetch_nearest_cache failed; prefilling in full", exc_info=True)
            return None, prompt_tokens

    def _insert_prefix(
        self, bundle: Any, loaded: LoadedModel, prompt_tokens: list[int], cache_obj: Any
    ) -> None:
        if bundle is None or cache_obj is None:
            return
        try:
            bundle.insert_cache(loaded.model, prompt_tokens, cache_obj)
        except Exception:
            log.debug("[mlx] insert_cache failed; cache not updated", exc_info=True)

    # -- errors ------------------------------------------------------------

    def _classify(self, exc: BaseException) -> BaseException:
        """Turn a worker exception into something the retry layer understands."""
        if isinstance(exc, (LLMTransportError, MlxNotAvailable)):
            return exc
        if isinstance(exc, asyncio.CancelledError):
            return exc
        if _is_oom(exc):
            # Unloading matters: a half-allocated resident model makes every
            # subsequent turn fail too. Schedule it, don't await it here.
            try:
                asyncio.create_task(self.unload(), name="mlx-oom-unload")
            except RuntimeError:  # pragma: no cover
                pass
            return LLMTransportError(
                f"mlx: out of memory during generation ({exc}); model unloaded — "
                "reduce the context window or pick a smaller quant",
                status_code=507,
            )
        return LLMTransportError(f"mlx: generation failed: {exc}")


def _resolve_tool_parser(name: str | None) -> Any:
    """Resolve a parser name to its callable.

    ``NEXUS_TOOL_PARSERS`` (formats mlx_lm doesn't ship) take precedence;
    otherwise the named module under ``mlx_lm.tool_parsers`` is imported and
    its ``parse_tool_call`` entrypoint used (pinned in mlx-lm 0.32.2; the
    older names below are a fallback list for mlx-lm drift). Returns ``None``
    when nothing usable is found — the provider then falls back to
    ``mlx_support.parse_tool_calls_fallback``.
    """
    if not name:
        return None
    from .mlx_support import NEXUS_TOOL_PARSERS

    local = NEXUS_TOOL_PARSERS.get(name)
    if callable(local):
        return local
    try:
        import importlib

        module = importlib.import_module(f"mlx_lm.tool_parsers.{name}")
    except Exception:
        log.warning("[mlx] tool parser module %r not importable", name)
        return None
    for attr in (
        "parse_tool_call", "parse", "parse_tools", "parse_tool_calls", "extract_tool_calls",
    ):
        fn = getattr(module, attr, None)
        if callable(fn):
            return fn
    log.warning(
        "[mlx] tool parser %r has no recognized entrypoint; "
        "falling back to the generic JSON parser",
        name,
    )
    return None


def get_runtime() -> MlxRuntime:
    return MlxRuntime.get()


def reset_runtime() -> None:
    """Tests only."""
    MlxRuntime.reset()
