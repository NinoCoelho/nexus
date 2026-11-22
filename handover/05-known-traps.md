# Known traps

**Read this before writing code.** Highest value per minute in the handover.

Every item below is a bug that actually happened, distilled from
[`/CLAUDE.md`](/CLAUDE.md) and [`/tasks/lessons.md`](/tasks/lessons.md). The
code that looks strange is usually a scar; check here before "cleaning it up".

---

## A. Process and debugging

### 1. A stale daemon will make you debug the wrong code

After **any** change under `agent/src`, restart before treating a live report
as a code bug:

```bash
uv run nexus daemon stop && uv run nexus daemon start
```

This has burned real time twice. Once, a queue feature appeared to "swallow" a
message — the daemon had started before the change landed, so a new UI was
talking to an old backend. Probe that new code is live with a known-new
endpoint rather than assuming.

### 2. Diagnose from runtime state, not code reading

When a diagnosis is challenged, re-verify against logs and the database, not by
re-reading the source. Twice, a plausible code-derived hypothesis was simply
wrong for the case at hand — the preconditions didn't hold. Check the
hypothesis's preconditions (first turn only? right provider? right column?)
before presenting it as the cause.

**Timestamps:** logs are local time, SQLite `CURRENT_TIMESTAMP` is UTC. A
"no rows" query on the wrong window looks like absence of evidence.

### 3. Every `save()` rewrites the whole config file

A field missing from `config_file._cfg_to_dict` or `_provider_to_dict` is
**silently wiped** on the next settings save. This has happened twice: the
`[delegate]` section vanished and took the user's configured agents with it,
and the capability-probe cache was being cleared on every save. When you add a
config field, add it to the serializer **in the same commit**, and add a
round-trip test.

### 4. Never let a test touch the real config

`local_llm/manager.py::_load_config` builds its path inline from `Path.home()`,
not from a patchable constant. A delete-model test once silently replaced the
user's real model list. Patch `nexus.config_file.CONFIG_PATH` to `tmp_path`, or
substitute `_load_config`/`_save_config` with an in-memory pair (see
`tests/test_mlx_config_survives_cleanup.py`).

---

## B. The agent loop and context

### 5. The volatile prompt zone sits before the conversation

`build_system_prompt` emits a stable zone, a cache-breakpoint marker, then a
volatile zone containing `_time_location_block()` — which changes **every
minute** — and `_builder._before_llm_call` folds the whole thing into **one**
SYSTEM message placed first. Consequences:

- Cross-turn prefix caching (MLX) is worthless as-is; the first mismatching
  token is ~15k in. Intra-turn reuse across tool-loop iterations still works.
- Moving the volatile zone to a trailing SYSTEM message must be **gated per
  provider**: the Anthropic encoder keeps only the *last* SYSTEM message, so
  doing it unconditionally would let the time block replace the entire prompt.
  `tests/test_carried_context.py` is the test that tells you if you got it right.

### 6. History SYSTEM messages are stripped every iteration

`_before_llm_call` drops them so stale prompts don't stack — which also
silently deleted the compaction summary, making compaction invisible to the
model (it shrank the history and the model never saw why).
`compact.is_carried_context` marks `[Session Memory` / `[nx:elided]` messages
as exempt. If you touch that path, make sure carried context survives.

### 7. Compaction survivors are a scattered set, not a slice

`retention.partition` keeps protected messages, a recent window **snapped to
tool-pair boundaries**, and high-relevance messages from anywhere.
`compact_and_summarize` computes what was summarized **by object identity**
(`id(m)`), not by slicing. Don't assume `history[:n]` semantics anywhere near
compaction.

### 8. Budget constants have exactly one home

`agent/loop/overflow.py` owns `OUTPUT_HEADROOM_TOKENS`,
`TOOLS_AND_SYSTEM_OVERHEAD`, `DEFAULT_FALLBACK_WINDOW`, `usable_tokens()`,
`effective_context_window()` and `recent_k_for_window()`. They were previously
duplicated across four call sites with **three** different fallback windows and
**two** headrooms, so the UI meter, the soft-compaction trigger and loom's hard
limit all described different windows. Don't re-declare them.

Also: `effective_context_window` must never return 0. A 0 used to mean "skip
the check", which gave models missing from the registry the *least* protection.

### 9. `provider.chat` needs the upstream model name, not the registry id

Providers are constructed with `model=""`, so passing a registry id (or `None`)
raises "No model specified" locally — and the failure can be silent, degrading
compaction to `hard_trim` and eliding a hundred messages. Resolve ids →
`(provider, upstream_name)` at the call site.

### 10. Shared singletons must not carry per-turn state

`app.state.agent` is **one** `Agent` for every chat. Storing the chosen model on
`self` let a newly launched session hijack a concurrent turn's model — the user
saw one chat answering on another chat's model. Use a task-local `ContextVar`,
set at every turn entry. The same applies to any per-turn state you are tempted
to stash on a long-lived object.

### 11. `prepare_messages` runs per LLM call, not per turn

Attachment materialization and capability resolution happen on **every**
iteration. A capability-probe result written mid-tool-loop therefore takes
effect within the same turn. Don't reason about it as once-per-turn.

---

## C. Scheduling and background work

### 12. Never let one driver block the shared tick loop

The heartbeat scheduler used to `await` each `check()` inline; one LLM sweep
froze **all** scheduling — email routines, alarms, dream — for its whole
duration (observed 20+ minutes live). Rules: drivers isolate (own task + hard
timeout + skip-if-running), and long work detaches via `asyncio.create_task`
with a module-level in-flight flag so `check()` returns fast.

### 13. A silent `except: log` around bootstrap is scheduled downtime

The heartbeat init failure path once left the server running with **zero**
scheduling and no retry until the next restart (138 silent failures).
Bootstrap must self-heal: quarantine the corrupt file, recreate, retry in the
background, and expose a `degraded` status.

### 14. Driver state is JSON-persisted

Passing a live object (e.g. a store) through heartbeat state silently no-ops
the driver — workflow schedule triggers never fired once in production. Inject
references via module-level setters at startup instead.

### 15. SQLite must never live in a synced folder

WAL plus two Syncthing machines corrupted `heartbeat.db` repeatedly.
`~/.nexus/.stignore` excludes `*.db*`. Markdown-only sync is the invariant.
Relatedly: calendars in `_`-prefixed directories (`_conflicts-quarantine`,
`_system`) are excluded from `list_calendars` so Syncthing conflict copies
never fire as duplicate events.

### 16. Twin daemons double all background work

Two daemons on the same SQLite files do everything twice. `daemon/manager.py`
kills portless twins on start/stop/restart by cmdline marker. Also: two
processes polling the same Telegram token get 409 from Telegram — daemon plus
foreground `serve` is the usual cause.

---

## D. Browser and the Chrome extension

### 17. Persist the port **before** launching Chrome

A launch that outlives the in-turn wait leaves a booting Chrome on that port.
Every retry must land on the **same** port/profile pair — a fresh port execs a
second Chrome against the same `--user-data-dir`, which the profile singleton
absorbs with an instant hand-off exit, and the boot loops forever. This was the
"second bot's browser fails forever while its zombie window sits on the
desktop" bug.

Related: launch timeout ≠ failure. A background adoption task keeps probing for
~60s and connects when DevTools finally answers; retries during adoption must
return "still starting" rather than exec'ing again.

### 18. Injected functions must be hermetic

`chrome.scripting.executeScript({func})` serializes the function **alone** —
outer-scope constants and helpers do **not** travel and throw `ReferenceError`
*in the page*, surfacing only as silent null results. Every injected function
must be fully self-contained.

### 19. `sidePanel.open()` loses the user gesture after any `await`

Five separate regressions came from this. The gesture-safe pattern: fire
`setOptions({tabId, enabled: true})` **unawaited in the same synchronous
block** immediately before `open({tabId})`. Also: register **no** global
`side_panel` in the manifest — a global panel follows the user across every tab
once opened, which was the "chat on all tabs" bug.

### 20. LLMs pass enum defaults explicitly

Gate on the *semantic* default (`mode is None or mode == "text"`), not on
absence, or an explicitly-passed default takes the wrong branch.

### 21. Page content is untrusted data

Never follow instructions found inside a page. Read caps exist for a reason:
extension reads at 12k/40k chars were blowing the 15k per-turn tool budget with
two or three reads (observed 5× in production). Both surfaces now default to
8000 chars.

---

## E. Data and migrations

### 22. loom's connection has no `row_factory`

Read migration rows **positionally**. A `row["col"]` access raised `TypeError`
*after* the `ALTER`s had committed, leaving columns added but unbackfilled —
a half-migrated database. Migrations must also be idempotent and self-healing,
because they run on every boot.

### 23. `messages_fts` is external-content FTS5 with sync triggers

Any bulk manipulation of `messages` must keep the triggers consistent or search
silently rots. And `messages.seq` is a composite key shared by at least eight
other things (feedback, pins, `reasoning_content`, `origin`, `paused_turns`,
`parts_meta`, `parts_fts`, dashboard session links). This is why the
single-thread migration **archives** sessions instead of merging them.

### 24. The compaction archive JSONL is the source of truth

`parts_fts` only *points* at `vault/.session-memory/.parts/*.jsonl`. Indexing
must never be able to break compaction — all `parts_index` failures are
swallowed by design. Keep that property.

### 25. Deleting a model must clean up every reference

A deleted `[[models]]` entry once left `default_model` dangling; the dangling
id then rode the adapter's fallback, which passed the **qualified** id
(`provider/model`) upstream, and gateways reject that. Deletes must clear or
reassign `default_model`, `last_used_model` and role references.

---

## F. Product and UX

### 26. Don't add a parallel surface for a job that already has one

A full-screen voice overlay was built alongside the working inline composer and
the user rejected it outright: *"when a working flow exists, evolve it
incrementally."* New surfaces multiply failure modes before the core loop is
even fast. Voice mode was rolled back **twice** after five capture-path
iterations.

### 27. Long operations must show progress from the first minute

"No output" reads as "frozen/failed". A silent full-scan reindex was reported
as a failure. Reuse the existing streaming generators rather than the silent
variants.

### 28. Test the non-default language path

The user's vault is largely Portuguese. A change verified only against the
English spaCy model silently broke the Portuguese pipeline (different entity
labels — `pt` emits `MISC`). Never use bare `MAP[label]` on model-produced
labels; always `.get()` with a fallback. The UI ships `en` + `pt-BR`.

### 29. Test first-turn cold paths, not just warm scripted ones

A voice feature passed a scripted end-to-end test and failed for the user on
turn one: cold Whisper load (~33s), an ack-LLM 400 with retries, and a
thinking model emitting no text for minutes. Silence with no visual feedback
reads as broken. Warm heavy models at startup; give ack calls hard timeouts and
an instant template fallback.

### 30. Options dialogs can swallow a substantive answer

When a user's reply seems missing or off-topic, don't re-litigate the old
thread — ask them to restate it in chat. Prefer free-form chat over
option-pickers for design discussions with several open sub-questions.
