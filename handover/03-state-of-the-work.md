# State of the work

What has been done, what is verified, and what you are picking up.

**Branch:** `handover/macos-port`, branched from `feat/macos-port-plan-and-mlx`,
which branched from `main` at `542bfb5`.

---

## 1. Honest statement of verification

All the code and documentation on this branch was written on a **Windows
machine with no Python environment, no Swift toolchain, and an npm registry
blocked by a corporate proxy.** Concretely:

| Check | Status |
|---|---|
| `py_compile` on every new/edited Python file | **passed** |
| Line length ≤ 100 (`ruff` config) on all new files | **passed** (0 violations) |
| Pure-module logic, exercised standalone | **passed** — see §3 |
| `uv run pytest` | **never run** |
| `uv run ruff check` | **never run** |
| `swift build` | **never run** |
| `npm run build` | **never run** (registry blocked) |
| Against a real MLX model | **never run** |

Treat the MLX implementation as a **reviewed draft that compiles**. Your first
task is §5.

## 2. Commits on this branch

| Commit | Contents |
|---|---|
| `06bb5fc` | docs: the port plan (now `handover/plan/`) + fixed documentation that referenced the deleted `features.py` |
| `87dad48` | feat(mlx): the in-process MLX provider — 8 new modules, 3 test files, 6 edited files |
| `0d211d8` | style(mlx): wrapped lines over the 100-char ruff limit |
| *(this one)* | the handover folder you are reading |

## 3. What was actually verified, and how

Two modules are pure stdlib by design, so they were loaded standalone (via
`importlib`, with a stubbed package tree) and their logic exercised directly.
Every assertion passed.

**`agent/llm/mlx_support.py`** — the riskiest code in the change, because its
whole job is handling sentinels that arrive split across token boundaries:

- think spans fed **one character at a time**, and whole-string
- a sentinel split across chunks (`"<"`, `"th"`, `"ink>"`) never leaking a
  fragment onto the prose channel
- `</think>` with no opener (templates that pre-open the span)
- `<thinking>` not mis-read as `<think>` + `"ing>"`
- tool-call text buffered, never emitted as prose
- unterminated spans flushing to the right channel
- multiple spans in one stream
- parser selection from architecture, and the chat-template fallback
- fallback tool-call parsing: objects, arrays, OpenAI wrappers, aliases,
  garbage (returns `[]`, never raises), and rejection of unknown tool names
- memory sizing: oversized rejected, in-budget accepted, 75% headroom enforced,
  non-Apple-Silicon permissive

**`local_llm/mlx_snapshot.py`** — reading capabilities off a HF snapshot:

- context window from `max_position_embeddings`, from the **VLM `text_config`
  nesting**, and with a rope-scaling factor applied (and ignored at factor ≤ 1)
- capabilities: tools from a template variable *or* a sentinel; vision from
  `vision_config` *or* architecture; reasoning from a think sentinel
- chat-template discovery: `chat_template.jinja` preferred, `tokenizer_config`
  fallback, and the **list-of-named-templates** form flattened
- malformed JSON and missing directories returning safe defaults, not raising
- slug generation, multi-shard byte summing, `to_public()` omitting the
  (potentially huge) template

## 4. The MLX implementation

Design and rationale: [`plan/ws1-mlx-provider.md`](plan/ws1-mlx-provider.md).

### New modules

| File | LOC | Role |
|---|---|---|
| `agent/llm/mlx_support.py` | ~430 | **pure.** Span splitter, tool-parser table, fallback parser, memory sizing |
| `agent/llm/mlx_runtime.py` | ~470 | process-global singleton: one resident model, one generation at a time, token bridge to the event loop, prefix caches, idle unload, OOM classification |
| `agent/llm/mlx_provider.py` | ~390 | `LLMProvider` impl: chat-template encoding, channel splitting, tool-call emission |
| `local_llm/mlx_snapshot.py` | ~260 | **pure.** Snapshot → capabilities, window, size |
| `local_llm/mlx_manager.py` | ~270 | install / activate / remove + config registration |
| `local_llm/mlx_downloader.py` | ~175 | `snapshot_download`, reusing the existing task registry and progress events |
| `local_llm/mlx_hf_search.py` | ~190 | `mlx-community` + `library=mlx` search, plus pre-download `config.json` inspection |
| `server/routes/mlx.py` | ~290 | `/local/mlx/*` |

### Edited files

| File | Change |
|---|---|
| `agent/registry.py` | a guarded `mlx` branch in `build_registry`, **plus** `"mlx"` in `_is_provider_functional`'s anonymous set — without the latter the model is filtered out of `available_model_ids` and silently never appears |
| `config_schema.py` | `ProviderConfig.model_path` |
| `config_file.py` | serialize `model_path` in `_provider_to_dict` — **omitting it would wipe local models on the next settings save** |
| `server/app.py` | register the router |
| `agent/pyproject.toml` | the `mlx` extra (platform-marked) + an `mlx` pytest marker |
| `CLAUDE.md`, `docs/features.md` | removed instructions referencing the deleted `features.py` |

### Tests written (never executed)

`tests/test_mlx_support.py`, `tests/test_mlx_snapshot.py`,
`tests/test_mlx_config_survives_cleanup.py`.

The third is the important one: it pins that MLX providers are named
`mlx-<slug>` and **survive `local_llm.manager.cleanup_stale_config()`**, which
deletes every `local`/`local-*` provider absent from its llama-server process
table. An in-process provider is never in that table, so the `local-` prefix
would make the user's model vanish from config on every boot.

### Three deliberate deviations from the plan

1. **No `catalog.json` entry, no change to the `routes/providers.py:616`
   runtime_kind allowlist.** That allowlist is inside the provider *wizard*
   handler, and MLX providers are created by `POST /local/mlx/activate`, not
   the wizard. Adding it would let someone hand-create a broken MLX provider
   with no `model_path`. `ProviderConfig.runtime_kind` is a plain `str`, so no
   `Literal` change is needed either.
2. **No `providers/probe.py` change.** `_resolve_endpoint` already returns
   `None` for any `runtime_kind` outside `{openai_compat, ollama}` (`:84`), so
   MLX is never queued for a capability probe. The planned short-circuit was
   redundant.
3. **No deletion of the ~1,940 LOC of verified-dead UI code**
   (`CoordinatorBubble/`, `useCoordinatorBubbleStatus.ts`,
   `InsightsView.css`). It is provably unreferenced, but the agreed sequencing
   puts pruning in the last milestone, and `npm run build` could not be run.
   Tracked as [`plan/fixes-backlog.md`](plan/fixes-backlog.md) #10.

### One unresolved unknown

The per-module entrypoint symbol in `mlx_lm.tool_parsers.*` could not be
verified without the package installed.
`mlx_runtime._resolve_tool_parser` tries `parse`, `parse_tools`,
`parse_tool_calls`, `extract_tool_calls`, `__call__` in order and falls back to
the generic JSON parser with a warning. **Read
`mlx_lm/tool_parsers/__init__.py` and pin the real name.**

## 5. Your first day

```bash
cd agent
uv sync --extra mlx

# 1. Your own baseline FIRST (6 failures are pre-existing).
uv run pytest --ignore=tests/live -q 2>&1 | tail -5

# 2. The new tests — these have never run.
uv run pytest tests/test_mlx_support.py tests/test_mlx_snapshot.py \
              tests/test_mlx_config_survives_cleanup.py -v

# 3. Lint the new code.
uv run ruff check src/nexus/agent/llm/mlx_*.py src/nexus/local_llm/mlx_*.py \
                   src/nexus/server/routes/mlx.py tests/test_mlx_*.py

# 4. Pin the tool-parser entrypoint.
python -c "import mlx_lm.tool_parsers as t; print(dir(t))"
ls "$(python -c 'import mlx_lm,os;print(os.path.dirname(mlx_lm.__file__))')/tool_parsers"
```

Then end-to-end: download a small model, activate it, run a turn **with tools**,
run a second turn, and confirm prefill reuse via `GET /local/mlx/status`
(`caches[...].by_type`). Also confirm a concurrent turn from another bot
**queues** rather than erroring.

## 6. What has not been started

Nothing below has any code on this branch.

| Workstream | Doc | Milestone |
|---|---|---|
| Shared Swift core + native app shell | [`plan/ws4-native-ui.md`](plan/ws4-native-ui.md) §1–2 | M1 |
| One chat thread per bot | [`plan/ws2-single-thread.md`](plan/ws2-single-thread.md) | M2 |
| Native chat | [`plan/ws4-native-ui.md`](plan/ws4-native-ui.md) §3 | M3 |
| Vault-as-Obsidian + browser revisit | [`plan/ws4-native-ui.md`](plan/ws4-native-ui.md) §4–5 | M5 |
| Dreaming → context curator | [`plan/ws3-dream-context-curator.md`](plan/ws3-dream-context-curator.md) | M6 |
| Prune + Apple-native integrations | [`plan/ws5-prune-and-integrate.md`](plan/ws5-prune-and-integrate.md) | M6 |
| 11 standalone fixes | [`plan/fixes-backlog.md`](plan/fixes-backlog.md) | any time |

**Recommended next milestone is M2 (one thread per bot), not M1.** It is
backend-only, testable against the existing suite and the existing web UI, and
it is the product decision most likely to need adjustment once lived with —
better to learn that before any native chat is written on top of it. The
chokepoint is three lines; see the ws2 doc.

## 7. Five errors made in the first draft of the plan

Corrected in the docs, listed here because each is an easy trap to re-enter.

1. `CACHE_BREAKPOINT_MARKER` lives in `agent/prompt_builder.py:31`, not
   `agent/llm/prompt_cache.py` (which only imports it).
2. `LLMProvider.chat_stream` has **no `extra_payload`**, and the loom adapter
   passes only `tools`/`model`/`max_tokens`. There is no per-call channel for
   "which bot" — use `agent/context.py::CURRENT_SESSION_ID`. Do not widen the
   signature (4 providers, the adapter, ~6 tests).
3. **Cross-turn prefix KV caching does not work as-is.** The whole system
   prompt is one SYSTEM message placed *first* (`_builder.py:290-293`), and the
   volatile zone inside it contains `_time_location_block()`
   (`prompt_builder.py:745`), which changes every minute. The first mismatching
   token is ~15k in, before turn 1. **Intra-turn** reuse across tool-loop
   iterations is the real v1 win and needs no changes outside the provider.
4. `agent/discovered_windows.py` is the wrong seam for context windows:
   `known_context_window` does a longest-**substring** match over
   `KNOWN_WINDOWS` *before* consulting discovery, and `"qwen3": 128_000` is a
   literal key (`overflow.py:227`). Use the `[[models]].context_window` config
   field, which `_builder._model_context_window:303-308` checks first.
5. An MLX provider named `local-*` is deleted on every boot by
   `local_llm/manager.py::cleanup_stale_config()` (`:897-940`).
