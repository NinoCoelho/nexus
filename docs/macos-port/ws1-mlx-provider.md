# WS1 — In-process MLX provider

**Audience: the developer implementing local Apple Silicon inference.** Read
[`README.md`](README.md) §3 ("five mistakes") first — three of the five are in
this workstream.

Milestone **M4**. Depends on [`ws2`](ws2-single-thread.md) only for the cache
key to be worth anything; otherwise independent.

## Implementation status

A first pass is **already committed**. It was written on a Windows machine with
no `uv`, no venv and no Swift toolchain, so: the pure modules were verified by
loading them standalone and exercising their logic (all assertions pass, see
below), everything compiles under `py_compile`, and **nothing has been run
under pytest or against a real model.** Treat it as a reviewed draft.

| Piece | File | State |
|---|---|---|
| Span splitter, parser table, fallback parser, memory sizing | `agent/llm/mlx_support.py` | **logic verified standalone** (think spans char-by-char, split sentinels, pre-opened `<think>`, tool buffering, parser selection, fallback parsing, fit checks) |
| Snapshot reader | `local_llm/mlx_snapshot.py` | **logic verified standalone** (window incl. VLM `text_config` nesting + rope scaling, capabilities, template discovery incl. list form, malformed/missing config) |
| Runtime (worker thread, lock, queue, cache, idle unload, OOM) | `agent/llm/mlx_runtime.py` | written, unrun |
| Provider (`LLMProvider` impl) | `agent/llm/mlx_provider.py` | written, unrun |
| Config registration + `local-*` landmine guard | `local_llm/mlx_manager.py` | written, unrun; tests written |
| Snapshot download | `local_llm/mlx_downloader.py` | written, unrun |
| HF search + pre-download inspection | `local_llm/mlx_hf_search.py` | written, unrun |
| `/local/mlx/*` routes | `server/routes/mlx.py` | written, unrun |
| Registry branch, `model_path` schema + serialization, `mlx` extra, pytest marker | `agent/registry.py`, `config_schema.py`, `config_file.py`, `pyproject.toml` | written, unrun |
| Tests | `tests/test_mlx_support.py`, `test_mlx_snapshot.py`, `test_mlx_config_survives_cleanup.py` | written, **never executed** |

**First thing to do on a Mac:**

```bash
cd agent
uv sync --extra mlx
uv run pytest tests/test_mlx_support.py tests/test_mlx_snapshot.py \
              tests/test_mlx_config_survives_cleanup.py
uv run ruff check src/nexus/agent/llm/mlx_*.py src/nexus/local_llm/mlx_*.py
```

**Two things deliberately left undone, and why:**

1. **No `catalog.json` entry and no change to the `routes/providers.py:616`
   runtime_kind allowlist.** That allowlist is inside the provider *wizard*
   handler, and MLX providers are not created by the wizard — they are created
   by `POST /local/mlx/activate`, which writes config directly. Adding `"mlx"`
   there would let someone hand-create a broken MLX provider with no
   `model_path`. `ProviderConfig.runtime_kind` is a plain `str`, so no `Literal`
   change is needed either. This is a deliberate deviation from the earlier
   plan, which called for both.
2. **No `providers/probe.py` change.** `_resolve_endpoint` already returns
   `None` for any `runtime_kind` outside `{openai_compat, ollama}` (`:84`), so
   MLX models are never queued for a capability probe. The planned
   short-circuit was redundant.

**The one thing that could not be verified from a non-Mac machine:** the
per-module entrypoint symbol in `mlx_lm.tool_parsers.*`.
`mlx_runtime._resolve_tool_parser` tries `parse`, `parse_tools`,
`parse_tool_calls`, `extract_tool_calls`, `__call__` in order and falls back to
the generic JSON parser with a warning. **Read
`mlx_lm/tool_parsers/__init__.py` on the Mac and pin the real name.**

## Why in-process rather than a supervised server

`mlx_lm.models.cache` exposes `make_prompt_cache`, `save_prompt_cache`,
`load_prompt_cache`, `trim_prompt_cache`, `can_trim_prompt_cache`, and
`LRUPromptCache.fetch_nearest_cache(model, tokens) -> (cache, remaining)`. A
real prefix KV cache is only reachable in-process; an HTTP server hides it.
That is the whole argument — if it turns out not to pay off, the subprocess
approach (mirroring `local_llm/manager.py`) is the fallback.

## Build order

Half of this runs on a non-Mac machine. Keep every pure function in modules
that do **not** import `mlx` at module scope; confine the import to a lazy
`_ensure_loaded()`.

```
S1-0  pyproject `mlx` extra (platform marker) + `mlx` pytest marker   no behaviour
S1-A  local_llm/mlx_snapshot.py   (config.json → caps/window/size)    pure · any OS
S1-B  agent/llm/mlx_support.py    (span scanner, parser table, fit)   pure · any OS
S1-C  agent/llm/mlx_runtime.py    (worker thread, lock, cache, unload)    Mac
S1-D  agent/llm/mlx_provider.py   (LLMProvider impl)                      Mac
S1-E  local_llm/mlx_manager.py + mlx_downloader.py + mlx_hf_search.py
      + the cleanup_stale_config predicate fix                            Mac
S1-F  registry / catalog.json / providers.py:616 / probe short-circuit
      / config_schema model_path                                      either
S1-G  server/routes/mlx.py  (parallel /local/mlx/* routes)            either
S1-H  [optional, separable] prefers_trailing_volatile_zone flag
      + _builder volatile-zone relocation                            either
```

## S1-0 Packaging

`mlx` / `mlx-lm` are macOS-arm64-only wheels. Declare them as an **extra** with
a platform marker inside it (belt and braces — the marker means even
`uv sync --extra mlx` on Linux resolves to nothing rather than failing):

```toml
# agent/pyproject.toml → [project.optional-dependencies]
mlx = [
    "mlx>=0.22; sys_platform == 'darwin' and platform_machine == 'arm64'",
    "mlx-lm>=0.21; sys_platform == 'darwin' and platform_machine == 'arm64'",
]
```

Never put these in `[project.dependencies]`. Follow the `bedrock` precedent
exactly: `agent/registry.py:179-188` wraps the import in `try/except
ImportError` and logs an install hint.

Add to `[tool.pytest.ini_options].markers`:
`"mlx: requires macOS arm64 + the mlx extra"`, and gate those tests with
`pytest.mark.skipif(sys.platform != "darwin", ...)`.

Do **not** add `mlx-vlm` (see §8).

## S1-1 Async / threading

MLX generation is synchronous, GIL-bound, and shares one Metal device, while
`app.state.agent` is a single `Agent` serving every chat.

**`MlxRuntime` is a process-global singleton**, not a per-provider-instance
object: `registry.build_registry` is rebuilt on every config change (called
from `routes/local_llm.py:304,338,377,428`), and a per-instance executor would
leak threads and reload the model each time.

```python
class MlxRuntime:
    _instance: MlxRuntime | None = None

    @classmethod
    def get(cls) -> MlxRuntime: ...

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="nexus-mlx")
        self._gen_lock = asyncio.Lock()           # serializes generation
        self._load_lock = asyncio.Lock()          # serializes load/unload
        self._loaded: _LoadedModel | None = None
        self._caches: dict[str, Any] = {}         # session_id -> LRUPromptCache
        self._last_used: float = 0.0
        self._idle_task: asyncio.Task | None = None

    async def generate_stream(
        self, *, model_path: str, prompt_tokens: list[int], max_tokens: int,
        cache_key: str | None, queue_timeout: float = 300.0,
    ) -> AsyncIterator[tuple[str, dict]]: ...
```

- **Serialize with a single `asyncio.Lock`**, mirroring
  `providers/probe.py:189`. An `asyncio.Queue` of jobs is more machinery for
  the FIFO semantics `asyncio.Lock` already gives you on CPython.
- **A second turn arriving mid-generation queues; it does not preempt or get
  rejected.** `ChatTurnRunner` already queues *same-session* messages, so the
  only concurrent case is a different bot or a background heartbeat turn —
  rejecting those shows the user an error for "the GPU is busy". Preempting is
  unsafe: `stream_generate` has no clean mid-step abort and you'd discard the
  prefill. Use `queue_timeout` (default 300s, config-surfaced) and raise
  `LLMTransportError("mlx: generation queue timed out after 300s", status_code=503)`
  so `error_classifier` marks it retryable.
- **Never `run_in_executor` the whole generation and await it** — that is one
  await for the entire turn and streaming dies. The worker thread runs the sync
  loop and hands tokens over with `loop.call_soon_threadsafe(queue.put_nowait, item)`;
  the async side awaits `queue.get()`.
- **Coalesce tokens on a ~4 ms / ~24-char boundary.** The cost is not the
  thread hop (~60 wakeups/s is nothing) — it's that each `delta` becomes an SSE
  frame plus a `TurnAccumulator.process_event` plus a pub-sub publish. The
  buffering also makes the sentinel tail-hold in §2 free.
- **Cancellation:** `job.cancel` is a `threading.Event` set from the async
  side's `finally`, so a client disconnect stops generation at the next token
  instead of running to `max_tokens`.

```python
def _worker(job: _Job, out: _ThreadQueue) -> None:
    for resp in stream_generate(model, tokenizer, prompt=job.prompt_tokens,
                                max_tokens=job.max_tokens, sampler=job.sampler,
                                prompt_cache=job.cache):
        out.put(("text", resp.text))
        if job.cancel.is_set():
            break
    out.put(("end", {"usage": ...}))
```

**Watchdog interaction.** `LoomProviderAdapter` aborts a stream silent for
`STREAM_IDLE_TIMEOUT_SECONDS = 180.0` (`agent/_loom_bridge/adapter.py:29,211`),
raising `LLMTransportError` — the same type as an httpx timeout, so the retry
machinery treats it as a transport fault. The adapter's docstring says it
deliberately does **not** bound the pre-first-event window, so a queued turn is
safe. But emit **nothing** while queued (a `delta` would pollute
`accumulated_text`) and let `routes/_sse.py:26-34`'s keepalive do its 20 s
`: ping`. If a cold load plus a large prefill ever trips the 180 s gap, emit
progress during prefill rather than lengthening the timeout globally.

## S1-2 Streaming, and where `thinking_delta` comes from

**Nothing in Nexus splits `<think>` spans today.** `agent/loop/reasoning.py`
only *carries* `reasoning_content` a provider already separated, and
`llm/openai.py:507` reads it from `delta.reasoning` / `delta.reasoning_content`.
MLX gives you one flat text stream, so **this provider must do the splitting**
and is the only one that has to.

Put the scanner in `agent/llm/mlx_support.py` (pure, no mlx import):

```python
@dataclass
class SpanSplitter:
    """Incremental scanner routing streamed text into prose / thinking / tool
    channels, holding back a tail so a sentinel split across token boundaries
    is never emitted as prose."""
    open_think: tuple[str, ...] = ("<think>", "<thinking>")
    close_think: tuple[str, ...] = ("</think>", "</thinking>")
    tool_open: tuple[str, ...] = ()      # derived from the chat template, §3
    tool_close: tuple[str, ...] = ()

    def feed(self, chunk: str) -> list[tuple[str, str]]:
        """-> [("prose"|"thinking"|"tool", text), ...]"""
    def flush(self) -> list[tuple[str, str]]: ...
```

Tail-hold size is `max(len(s) for s in all_sentinels) - 1`: hold that many
trailing characters back from every emission and release them in `flush()`.
One mechanism covers both think tags and tool sentinels.

Map `prose` → `{"type":"delta","text":...}`, `thinking` →
`{"type":"thinking_delta","text":...}`, `tool` → buffered and never emitted as
an event until parsed. Accumulate `full_text` / `full_reasoning` and set
`reasoning_content=full_reasoning or None` on the `finish` event, mirroring
`llm/openai.py:556-564` field for field.

**Edge case:** some MLX reasoning models emit `</think>` with no opening tag
because the template pre-opens it. Default `in_thinking=False` but accept
`starts_in_thinking: bool`, computed once at load time by checking whether
`tokenizer.apply_chat_template([], add_generation_prompt=True, tokenize=False)`
ends with an open think sentinel. Store it on `_LoadedModel`.

## S1-3 Tool calling

**(a) Parser selection, at load time.** Pin the mapping in our own table so an
`mlx_lm` refactor is a one-line fix rather than a mystery:

```python
# mlx_support.py
_TOOL_PARSER_BY_ARCH: dict[str, str] = {
    "qwen3": "qwen3_coder", "qwen3_moe": "qwen3_coder",
    "gemma3": "gemma4", "glm4": "glm47", "kimi": "kimi_k2",
    "minimax": "minimax_m2", "mistral": "mistral", "llama": "pythonic",
    # ...
}

def select_tool_parser_name(
    *, architectures: list[str], model_type: str, repo_id: str, chat_template: str
) -> str | None:
    """Arch/model_type table first; then chat-template sniffing
    (`<tool_call>` → json_tools, `[TOOL_CALLS]` → mistral,
    `<|python_tag|>` → pythonic); then None."""
```

Resolve the callable lazily via
`getattr(importlib.import_module(f"mlx_lm.tool_parsers.{name}"), "<entrypoint>", None)`
in a `try/except`. **The per-module entrypoint symbol is the one thing not
verified from the planning machine — read `mlx_lm/tool_parsers/__init__.py` on
the Mac and pin the real name before implementing.** Design against
`Callable[[str, list[dict]], list[dict]]` and adapt. The available modules are:
`function_gemma, gemma4, glm47, json_tools, kimi_k2, kimi_k3, laguna, longcat,
minicpm5, minimax_m2, mistral, muse_spark, pythonic, qwen3_coder`.

**(b) Prompt construction.**

```python
tokens = tokenizer.apply_chat_template(
    chat_messages,                         # OpenAI-shaped dicts
    add_generation_prompt=True,
    tools=[_encode_tool(t) for t in tools] if tools else None,
    tokenize=True,
)
```

Import `_encode_msg` / `_encode_tool` from `llm/openai.py` rather than
reimplementing — the chat template consumes exactly OpenAI shape, and you get
`tool_calls`, `tool_call_id` and `reasoning_content` round-tripping for free.
**Strip the cache marker** (`strip_cache_marker` from `..prompt_builder`) on
every string `content` first, as `openai._build_payload:275-281` does. The
marker must never reach a tokenizer.

Guard: `apply_chat_template(..., tools=...)` raises `TemplateError` for
templates with no `tools` variable. Catch it, log once per model, retry without
`tools=`, and set `_LoadedModel.supports_tools = False` — which §8 reads.

**(c) Emission.** Three details that are easy to get wrong:

- **Emit the whole span, not incremental deltas.** `mlx_lm`'s parsers are
  whole-span parsers. `openai.py:537-540` streams genuine deltas only because
  the upstream provides them; we don't have them, and the only consumer that
  would notice is an "args typing in" affordance the macOS client doesn't need.
- **`finish_reason` must be Nexus's `StopReason` value — `"tool_use"`, not
  OpenAI's `"tool_calls"`.** `_loom_bridge/message.py` maps on the Nexus enum.
- The `finish` event is where `ToolCall.arguments` becomes the parsed **dict**
  that `llm/types.py:43` requires.

```python
raw = splitter.tool_text()
parsed = tool_parser(raw, tool_dicts) if tool_parser else _fallback_json_tools(raw)
for i, tc in enumerate(parsed):
    tc_id = tc.get("id") or f"mlxtc_{i}"
    yield {"type": "tool_call_start", "id": tc_id, "name": tc["name"]}
    yield {"type": "tool_call_delta", "id": tc_id, "args_delta": json.dumps(tc["arguments"])}
    yield {"type": "tool_call_end", "id": tc_id}
yield {"type": "finish",
       "finish_reason": "tool_use" if parsed else "stop",
       "content": full_prose,
       "tool_calls": [...],            # arguments as parsed dicts
       "usage": usage.model_dump(),
       "reasoning_content": full_reasoning or None}
```

## S1-4 The prompt-cache contract

- **Cache key.** There is no per-call bot parameter on `chat_stream`
  (README §3.2), so key `LRUPromptCache` entries off
  `agent.context.CURRENT_SESSION_ID`. [`ws2`](ws2-single-thread.md)'s
  one-stable-session-per-bot is what makes that key long-lived and worth
  caching at all.
- **No invalidation hook is needed.** `LRUPromptCache` is content-addressed by
  token prefix: a compaction that rewrites the head simply yields
  `remaining == len(tokens)` — a full re-prefill, never a stale-cache
  correctness bug. You only need a memory bound: `LRUPromptCache(max_size=4,
  max_bytes=<from hardware.probe()>)`, with `stats_by_type()` / `nbytes`
  surfaced on the status route.
- **The v1 win is intra-turn, and it is large.** A tool loop runs up to
  `max_iterations` LLM calls whose message lists are strictly append-only
  (assistant message + tool results appended), so iterations 2..N hit the cache
  for the whole window. On a 10-iteration turn with a 40K window that is
  ~360K tokens of prefill avoided. **This needs no changes outside the
  provider — ship it.**
- **Cross-turn reuse is blocked today** (README §3.3). Unlocking it is S1-H and
  is optional: add `LLMProvider.prefers_trailing_volatile_zone: bool = False`,
  set `True` only on `MlxProvider`, and in `_builder._before_llm_call` emit
  `[SYSTEM(stable), *messages, SYSTEM(volatile + carried)]` instead of one
  leading SYSTEM. **It must be gated:** the Anthropic encoder keeps only the
  last SYSTEM message (`_builder.py:276-279` says so explicitly), so ungated
  this would let the time block replace the entire prompt. The test that tells
  you whether the relocation is correct is `tests/test_carried_context.py`.
- **Do not persist the cache to disk.** Explicitly rejected. A KV cache for a
  30B 4-bit model at 32K is multi-GB *per bot*, competing with the resident
  model for the same unified memory; reload is often slower than re-prefill on
  Apple Silicon; and it is only valid for a byte-identical prefix, which embeds
  the skills catalog, tool disclosure, credential and bot-roster blocks — all
  most likely to have changed at exactly the moment a warm start would help. A
  v2 option is to persist *only* the stable-zone prefix as one shared file
  keyed by its hash.

## S1-5 Capabilities and context window — via config, no new seam

Read the truth offline from the HF snapshot. New file
`agent/src/nexus/local_llm/mlx_snapshot.py`:

```python
@dataclass
class MlxModelInfo:
    repo_id: str
    local_dir: Path
    architectures: list[str]
    model_type: str
    max_position_embeddings: int
    quantization_bits: int
    is_vlm: bool               # vision_config present, or arch in a VLM set
    capabilities: list[str]
    total_bytes: int

def read_snapshot(local_dir: Path) -> MlxModelInfo:
    """Parse config.json + (optional) chat_template.jinja / tokenizer_config.json.
    max_position_embeddings falls back to text_config.max_position_embeddings
    (VLM nesting), then a rope_scaling-derived value, then 0."""
```

Capabilities, all offline: `"chat"` always; `"tools"` iff the chat template has
a `tools` variable or a tool sentinel; `"vision"` iff `config.json` has
`vision_config` or `architectures` matches a VLM; `"reasoning"` iff the
template contains a think sentinel.

Then `mlx_manager.add_to_config` writes onto the `[[models]]` entry:

- `context_window = max_position_embeddings` → `_builder._model_context_window`
  checks config **first** (`_builder.py:303-308`), bypassing the substring trap
  in README §3.4 entirely.
- `tags = ["local","offline","mlx", *capabilities]` →
  `catalog._user_config_capabilities` already unions them
  (`providers/catalog.py:242`), so `_payload.prepare_messages` stops dropping
  image parts.

**Also short-circuit `providers/probe.py`** so MLX models are never queued —
cheapest correct edit is to treat an entry whose tags contain `"mlx"` as
already decided in `CapabilityProbeService`. There is no endpoint to probe.

**Do not touch `agent/discovered_windows.py`** — it is an LM Studio HTTP poller
with no URL for an in-process provider, and it sits after the substring match.

## S1-6 Model lifecycle and memory

```python
async def ensure_loaded(self, model_path: str) -> _LoadedModel
async def unload(self) -> None       # drop refs, self._caches.clear(), mx.clear_cache()
def fits(self, model_path: str) -> tuple[bool, str]
```

- **One model resident at a time.** `ensure_loaded(p)` with a different path
  must `await self.unload()` first — loading a second while the first is
  resident is how you OOM a Mac. Log at INFO; it's a multi-second user-visible
  event.
- **Fit check.** `local_llm/hardware.py:22-61` already gives `ram_gb`,
  `vram_gb` (unified memory), `is_apple_silicon`, `recommended_max_params_b`.
  Add to `mlx_support.py`:
  ```python
  def estimate_resident_gb(snapshot_dir: Path, *, context_tokens: int) -> float:
      """sum of *.safetensors sizes + KV estimate from config.json
      (num_hidden_layers * num_key_value_heads * head_dim * 2 * 2 bytes
       * context_tokens) + ~1.5 GB slack."""
  def fits_in_unified_memory(need_gb: float, hw: dict) -> tuple[bool, str]
  ```
  **Gate at activate, not at generation** — a refusal at generation time costs
  the user their turn. Return 409 with the numbers from
  `POST /local/mlx/activate`.
- **Eviction.** Idle timeout, default 900 s, config key
  `[agent].mlx_idle_unload_seconds`. One `asyncio.Task` rescheduled on each
  generation (`self._last_used = time.monotonic()`), not a per-call timer. Wrap
  the unload in `try/except` and log — a failed unload must not wedge the
  runtime.
- **OOM.** Metal allocation failure is not always a clean Python exception
  type. Catch broad `Exception` in the worker; in the async wrapper, if
  `"out of memory"` / `"insufficient memory"` / `"MTLBuffer"` appears in the
  message, `await self.unload()` **then** raise
  `LLMTransportError("mlx: out of memory; model unloaded", status_code=507)`.
  Unloading is the important part — a half-allocated resident model makes every
  subsequent turn fail too. Put an actionable hint in the message ("reduce
  context_window or pick a smaller quant").
- **macOS ≥ 15** is needed for large-model memory wiring. Check once at load:
  if `platform.mac_ver()[0]` major < 15 and `estimate_resident_gb > 0.6 *
  ram_gb`, log a warning naming the requirement. Don't hard-fail.

## S1-7 Registry wiring

Four declaration points, plus one deliberate non-change.

1. **`agent/registry.py:101-268`** — new `mlx` branch inserted *before* the
   api-key block (MLX is anonymous, like the `ollama` branch at `:125`). Reads
   `model_path` off the provider config; no `base_url`. Wrap the import in
   `try/except ImportError` with an install hint, as `bedrock` does at
   `:179-188`.
   ```python
   if provider_type == "mlx":
       try:
           from .llm.mlx_provider import MlxProvider
       except ImportError:
           log.warning("[provider] %s: mlx requires the 'mlx' extra "
                       "(`uv sync --extra mlx`) — skipping", name)
           continue
       model_path = getattr(pcfg, "model_path", "") or ""
       if not model_path:
           log.warning("[provider] %s: mlx requires model_path — skipping", name)
           continue
       reg.register_provider(name, MlxProvider(model_path=model_path, model="",
                                               temperature=cfg.agent.temperature))
       log.info("[provider] %s initialized (mlx, in-process)", name)
       continue
   ```
2. **Also add `"mlx"` to `_is_provider_functional`'s anonymous set**
   (`registry.py:63-98`) — otherwise the model is filtered out of
   `available_model_ids` and silently never appears.
3. **`providers/catalog.py:103-110`** `RuntimeKind` Literal + a `catalog.json`
   entry. The entry **must** carry a non-empty `auth_methods`
   (`[{"id":"anonymous","label":"No credentials"}]`) — `load_catalog` validates
   that at `catalog.py:198`.
4. **`server/routes/providers.py:616`** — add `"mlx"` to the runtime_kind
   allowlist (currently hardcoded to `{openai_compat, anthropic, ollama}`).
5. **`config_schema.py` / `config_file.py`** — add `model_path: str = ""` to
   the provider model. `config_file._parse` does the `type`→`runtime_kind`
   migration, so it is the authority; check both.

**Leave the four direct `OpenAIProvider` constructions alone** (`main.py:95`,
`main.py:205`, `graphrag_manager/_resolvers.py:175`,
`heartbeat_drivers/dream_trigger/driver.py:210`). They are last-resort env-var
fallbacks reached only when the registry has no usable model, and that is
correct: a user whose MLX model failed to load should get a clear "no model
available", not a silent second attempt at the thing that just failed. **State
this in the PR so a reviewer doesn't "fix" it.**

## S1-8 Vision — defer `mlx-vlm` out of v1

Nexus's one non-negotiable requirement of a chat provider is tool calling: the
core set advertises ~16 tools on every turn and the whole loop is tool-driven.
`mlx-vlm`'s Python-level tool-calling support is **unverified**, and its
headline value (its own OpenAI-compatible server, continuous batching, prefix
caching) is a *server* — the architecture we deliberately rejected. Shipping an
unverified tool path for the primary chat provider is the wrong first bet.

- **The v1 fallback already exists and costs nothing.**
  `multimodal.materialize_messages` lowers any part the model can't handle to a
  text breadcrumb, so a snapshot with no `vision_config` gets images downgraded
  automatically — the same path a non-vision GGUF takes today. Second layer:
  `ocr.py` + the `ocr_image` tool + `[agent].vision_model` let the user keep a
  cloud vision model while text runs locally. Document that as supported.
- **v2, if it lands:** a separate `MlxVlmProvider` class sharing `MlxRuntime`
  (same lock, same Metal device — two runtimes would contend invisibly) under
  `runtime_kind: "mlx_vlm"`. Separate class, not a branch: the two libraries
  have different `load`/`generate` and cache APIs, and an `if self._is_vlm:`
  threaded through 400 lines of streaming/tool logic is how one of the two
  paths ends up permanently broken.
- **Refuse at activation.** If `supports_tools` is False,
  `POST /local/mlx/activate` returns 409 when the model is being set as
  `[agent].default_model`, while still allowing it as `[agent].vision_model`
  (where tools are never used). A toolless model cannot be Nexus's chat model.

## S1-9 Model-management surface — parallel routes, not a discriminator

**Add parallel `/local/mlx/*` routes. Do not add a `runtime` discriminator to
the existing 14 `/local/*` routes.** `GET /local/installed`
(`routes/local_llm.py:169-231`) returns a GGUF-shaped record whose unit of
identity is a **filename** (`filename`, `is_mmproj`, `has_mamba_layers`,
`has_mtp`, `mtp_draft_n`, `is_shard_group`, `parts`, `complete`, `port`).
MLX's unit of identity is a **snapshot directory / repo_id**, and `port` is
structurally absent for an in-process provider. A discriminator makes every
field conditionally valid and reshapes the fixtures in
`test_local_llm_shards.py`, `test_local_llm_mmproj.py`,
`test_local_llm_mtp.py` and `test_hf_search.py`. Parallel routes cost one
module and break nothing.

New file `agent/src/nexus/server/routes/mlx.py`, registered in
`server/routes/__init__.py` alongside `local_llm`:

| Route | Behaviour |
|---|---|
| `GET /local/mlx/available` | `{"available": bool, "reason": str}` — is the `mlx` extra importable. The UI hides the section when false. |
| `GET /local/mlx/hf/search?q=&limit=` | `HfApi().list_models(search=q, author="mlx-community", ...)`, plus a second pass with `library="mlx"` so non-`mlx-community` MLX repos are findable |
| `GET /local/mlx/hf/repo/{owner}/{repo}` | `repo_card` + `config.json` via `hf_hub_download` → quant bits, params, window, `fits_in_ram`. This is what makes a 20 GB download an informed choice. |
| `POST /local/mlx/download` | `{"repo_id": ...}` → `mlx_downloader.start_snapshot_download` |
| `GET /local/mlx/downloads`, `POST /local/mlx/download/{id}/cancel` | same `DownloadTask` dataclass and `event_bus` publish shape as `downloader.py`, so the UI progress component is reused unchanged |
| `GET /local/mlx/installed` | enumerate `~/.nexus/models/mlx/*/` → `read_snapshot()` + `is_active` |
| `POST /local/mlx/activate` | fit check → `add_to_config` → `_rebuild_registry` (same 3-step dance as `local_llm.py:292-307`) |
| `POST /local/mlx/deactivate` | `remove_from_config` → `MlxRuntime.get().unload()` → `_rebuild_registry` |
| `DELETE /local/mlx/installed/{repo_slug}` | deactivate, then `shutil.rmtree` |
| `GET /local/mlx/status` | resident model, `stats_by_type()`, `nbytes`, queue depth, last-used — needed for debugging and the UI's "model loaded" chip |

`/local/hardware` is runtime-agnostic — **reuse it**, don't duplicate.

New file `local_llm/mlx_downloader.py` wraps
`huggingface_hub.snapshot_download(repo_id, local_dir=...,
allow_patterns=["*.json","*.safetensors","*.txt","*.model","*.jinja"],
tqdm_class=_DownloadTqdm)`. **Import `downloader.py`'s `_DownloadTqdm`,
`_TASKS`/`_TASKS_LOCK`, `_publish`, `_cancelled` and `_abort_xet` rather than
reimplementing** — those encode hard-won Xet-cancellation behaviour
(`downloader.py:216-228`).

New file `local_llm/mlx_manager.py`:

```python
MLX_MODELS_DIR = Path.home() / ".nexus" / "models" / "mlx"

def slug_for(repo_id: str) -> str       # "mlx-community/Qwen3-30B-4bit" -> "qwen3-30b-4bit"
def provider_name(slug: str) -> str     # f"mlx-{slug}"   <-- NOT local-
def list_installed() -> list[MlxModelInfo]
def add_to_config(info: MlxModelInfo) -> str      # returns f"mlx-{slug}/{slug}"
def remove_from_config(slug: str) -> None
def cleanup_stale_config() -> None      # drops mlx-* whose snapshot dir is gone
```

`add_to_config` mirrors `manager.add_to_config:766-873` but writes
`{"runtime_kind": "mlx", "model_path": str(local_dir)}` into
`providers.mlx-<slug>` — **no `base_url`** — and the `[[models]]` entry from
§S1-5.

**And fix the landmine (README §3.5):** in `local_llm/manager.py`, both
`cleanup_stale_config()` (`:910-911`) and the model filter (`:916-926`) glob on
`local`/`local-`. The `mlx-` prefix already makes `startswith("local-")` false,
so verify nothing else in those functions globs loosely on `"local"`, and add
`tests/test_mlx_config_survives_cleanup.py` as a regression.

## Verification

```bash
# Any OS — the pure modules
uv run pytest tests/test_mlx_snapshot.py tests/test_mlx_support.py \
              tests/test_mlx_config_survives_cleanup.py tests/test_mlx_routes.py
```

New tests to write:

- `test_mlx_support.py` — `SpanSplitter`: a think tag split across chunk
  boundaries, a tool sentinel split across boundaries, `</think>` with no
  opening tag, malformed/nested spans, and the parser-selection table.
- `test_mlx_snapshot.py` — `config.json` → capabilities / window / size,
  including the VLM `text_config` nesting fallback.
- `test_mlx_config_survives_cleanup.py` — the README §3.5 regression.
- `test_mlx_routes.py` — 404/409 paths with `mlx` unavailable (runs anywhere).
- Mac-only, `@pytest.mark.mlx`: `test_mlx_runtime.py`,
  `test_mlx_provider_stream.py`.
- Extend `test_provider_registry.py`, `test_provider_catalog.py`,
  `test_provider_wizard_route.py` for the new `runtime_kind`.

On a Mac: a real tools turn against an `mlx-community` 4-bit model, then a
second turn — confirm intra-turn prefill reuse via
`LRUPromptCache.stats_by_type()` and that a concurrent turn from another bot
**queues** rather than failing.
