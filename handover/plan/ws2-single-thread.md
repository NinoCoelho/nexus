# WS2 — One chat thread per bot

**Audience: the developer implementing the UX change that motivates the whole
port.** Backend only; testable against the existing web UI and the existing
suite. Milestone **M2**, and it should land *before* any native chat is written
— it is the decision most likely to need adjustment once lived with.

The goal, in the product owner's words: *"agents/bots with single chat windows,
which manage their context without losing track… the ability to have multiple
chats is causing confusion."*

## The chokepoint is three lines, not nine subsystems

There is **no `POST /sessions` route**. Visible sibling sessions are created in
exactly three places:

| Site | Call |
|---|---|
| `server/routes/chat.py:333` | `store.get_or_create(req.session_id, context=…, project_id=req.project_id)` |
| `server/routes/chat_stream.py:107` | same |
| `tools/context_tool.py:242` | `create_child(hidden=False)` |

Change those and the feature is delivered. Everything else that touches
sessions — the coordinator, Telegram bindings, subagents, workflow/calendar/
kanban runs, `vault_dashboard.chat_session_id` — operates on an **explicit
session id** and keeps working untouched.

In both chat routes:

```python
if req.session_id is None and req.project_id:
    session = store.main_session_for_bot(req.project_id)
else:
    session = store.get_or_create(req.session_id, context=…, project_id=req.project_id)
```

Keep `get_or_create(None)` working for the no-bot case — the unassigned chat
and the coordinator master must not be forced through a bot.

## Data model: an explicit column, not a derived query

Add `projects.main_session_id TEXT NOT NULL DEFAULT ''`.

Deriving "most recently updated visible session per project" is tempting and
wrong *for this schema specifically*: `sessions.project_id` is also carried by
hidden workflow/calendar/kanban sessions, by `team` group chats, by archived
sessions, and by `fork_session` children (which pass `project_id` through,
`context_tool.py:246`). The derivation becomes a query with four `COALESCE`
clauses that silently returns the wrong thread the first time someone
group-chats or un-archives something — and there are no foreign keys to
protect you. An explicit pointer plus a repair pass is more robust.

Files:

- **`server/session_store/schema.py`** — add the column to `_PROJECTS_SCHEMA`
  (`:188-200`) **and** an `if "main_session_id" not in cols:` ALTER in
  `_ensure_bot_columns` (`:299-301`). Both, exactly as the `model` column does.
  Note `_ensure_bot_columns`' backfill `SELECT` names its columns, so adding
  one is harmless there — but keep reading rows **positionally**: loom's
  connection has no `row_factory`, and a previous `row["col"]` access raised
  `TypeError` *after* the ALTERs had committed, leaving columns added but
  unbackfilled.
- **`server/project_store.py`** — add `main_session_id: str = ""` to `Project`
  (`:22-37`) and a guarded read in `_row_to_project` (`:279`), copying the
  pattern already used for `model`:
  ```python
  try:
      model = row["model"] or ""
  except (KeyError, IndexError):
      model = ""
  ```
  Plus `set_main_session(project_id, session_id)` and
  `main_session_id(project_id)`.
- **`server/session_store/store.py`** — add:
  ```python
  def main_session_for_bot(self, project_id: str) -> Session:
      """The bot's single visible thread. Returns the pointer's session,
      adopting the most-recently-updated visible candidate when the pointer is
      empty or dangling, and creating one only when the bot has no thread at
      all. Self-healing: `delete(session_id)` is user-reachable, so a dangling
      pointer must never brick a bot."""
  ```

## Migration: pick one, archive the rest. Never merge.

New file `server/session_store/migrate_single_thread.py`:

```python
def adopt_main_threads(db) -> dict[str, int]:
    """One-shot, idempotent. For each project with an empty main_session_id:
    pick the most-recently-updated session that is visible, not archived, has
    no team JSON, and has >0 messages; set it as main_session_id; then
    mark_archived(True) on every other visible non-team session of that
    project. Sessions with project_id IS NULL are untouched."""
```

Call it from `init_fts` right after `_ensure_bot_columns(db)`
(`schema.py:424`) so it runs once per boot and is a no-op thereafter. Log one
INFO line with the counts.

**Why archive and not merge.** `messages.seq` is a per-session sequence and
`messages_fts` is an **external-content FTS5** table with insert/delete/update
triggers, so re-inserting rows under a new `session_id` with renumbered `seq`
must keep those triggers consistent or search silently rots. Worse, `seq` is a
composite key used by eight other things: `message_feedback` (PK
`(session_id, seq)`), `messages.reasoning_content` and `messages.origin`
(written by `replace_history` by seq index), `paused_turns.session_id` (PK),
`parts_meta.session_id` (PK), `parts_fts.session_id`,
`telegram_bindings.active_session_id`, `vault_dashboard.chat_session_id`, and
trajectories. A merge is an eight-table hand-written migration whose failure
mode is silently corrupted history — not worth saving the user a scroll.
Archived sessions stay in `messages_fts` and remain reachable via
`history_recall`, which is the point.

**Dry-run first.** The real blast radius is unknown until it runs on a machine
with actual data. Expose the same logic behind a `--dry-run` that reports, per
bot, how many visible sessions exist and which would be adopted, and read that
output before the first real run.

## Visibility: what changes, what doesn't

| Creator | Decision |
|---|---|
| **`fork_session`** | **Unregister it.** See below. |
| `SessionStore.list`/`count` + the 20 `routes/sessions.py` routes | **No change.** All take an explicit sid; `list` becomes the archive/history view, which is the right role for it. |
| Coordinator (`nexus_sessions`, `session_dispatch`, `[coordinator].session_id`, `adopt_dm_bindings`) | **No change.** The coordinator session is deliberately not a bot thread; `is_coordinator(sid)` still works. `nexus_sessions action="sessions"` now mostly returns one row per bot — an improvement. One doc tweak: its description says "recent chats"; make it "bot threads and internal sessions". |
| Telegram `active_session_id` | **Repoint, keep the column.** `/new` and `/switch` resolve to `main_session_for_bot(project_id)`; `/chats` becomes a bot picker. The mutable pointer stays because group/topic bindings legitimately map many Telegram threads onto bots. |
| Subagents (`app_subagents.py:47`) | No change — already `hidden=True`, already surfaced by `list_activity`. |
| Workflow / calendar / kanban runs, `vault_dashboard.chat_session_id` | No change — already `hidden=True` + `origin`-tagged. `SessionStore.list_activity(project_id)` (`store.py:406-430`) already exists to render them per bot. |
| Teams / round-robin | **Park.** A group chat with fan-out passes is the opposite of "one window per bot". |
| Per-sid artifacts (`.session-memory/{sid}.md`, `.parts/{sid}.jsonl`, `parts_meta` PK, `paused_turns` PK) | No change — **and this is the biggest argument for the whole design.** With one stable sid per bot, `{sid}.md` *becomes* the bot's durable memory file with zero new machinery; these artifacts stop being orphaned per-fork fragments. |
| `Agent._seed_session_notes` | **Keep.** It still covers resume-after-truncation and `replace_history`; removing it because forks are gone is churn with a correctness downside. |

### `fork_session`: unregister it

With a single endless thread, fork's unique capability — carry a summary into a
fresh window — *is* what continuous compaction does, and compaction does it
better by keeping the recent working set verbatim. Its only other capability,
isolated work in a fresh context, is `spawn_subagents`, which already uses
`create_child(hidden=True)`. Keeping fork as a hidden branch builds a second,
worse `spawn_subagents`. And its prompt text literally instructs the model to
*"Tell the user the new chat exists"* (`prompt_builder.py:476-481`) — that
sentence **is** the confusion being removed.

Edits:

1. `agent/_loom_bridge/registry.py:517` — remove `(FORK_SESSION_TOOL,
   handle_fork_session)` from `_TOOL_TABLE`, and its import.
2. `agent/tool_discovery.py:105` — remove `"fork_session"` from
   `_DEFAULT_CORE`. **Do both** — leaving it in the table but out of the core
   set makes it `tool_search`-discoverable, which is worse than either.
3. `agent/prompt_builder.py:476-481` — delete the bullet. Also `:493-494`:
   *"At phase boundaries, persist key state to the vault, then fork to a fresh
   session with a summary"* → *"persist key state to the vault; your thread's
   context is managed for you."*
4. `tools/context_tool.py:80-93` — the `orange` and `red` recommendation
   strings both say "Use fork_session"; rewrite to vault-write +
   `history_recall`.
5. **Keep `FORK_SESSION_TOOL` and `handle_fork_session` as unregistered code**
   so `tests/test_fork_session.py` (6 tests) passes unchanged. Add one test
   asserting the name is absent from both `registry.specs()` and
   `_DEFAULT_CORE`. Delete the code a release later.

## Never losing track

One endless thread makes compaction continuous rather than occasional, so the
thread's long-term state has to become legible.

### Three files, three owners

**Do not make `.session-memory/{sid}.md` user-editable.** It is tempting and it
is wrong: `persist_session_summary` does a `path.write_text(...)` **full
overwrite** on every compaction (`summarize.py:499`), so a user edit would be
silently destroyed, usually within the hour, on a file we had just called
"your bot's memory". That is the worst kind of feature.

| File | Owner | Role |
|---|---|---|
| `.session-memory/{sid}.md` | machine, overwritten | rolling summary injected via `_seed_session_notes` / `is_carried_context`. **Unchanged.** |
| `vault/{bot.vault_path}/thread-journal.md` | machine, append-only + trimmed | the new durable, legible audit trail |
| `vault/{bot.vault_path}/**` + `memory_tool` | **user and bot, editable** | already exists, already in the prompt (`prompt_builder.py:791-793`). This is where user-editable bot memory belongs — don't invent a second one. |

### The thread journal

New file `agent/loop/thread_journal.py`, a near-copy of
`coordinator_sweeps.py` (129 LOC — the right size) generalized on a path:

```python
MAX_ENTRIES = 60          # ~months of a thread at one compaction/day

def journal_path(vault_subdir: str) -> Path
def append_entry(vault_subdir: str, *, body: str, tokens_before: int = 0,
                 tokens_after: int = 0, messages_summarized: int = 0,
                 reason: str = "compaction", model_id: str | None = None) -> str | None
def load_last_entry(vault_subdir: str) -> str | None
def _trim(entries_block: str, max_entries: int = MAX_ENTRIES) -> str
```

Reuse `coordinator_sweeps`'s `_ENTRY_RE = re.compile(r"^## \d{4}-\d{2}-\d{2}",
re.M)` + head-keeping trim verbatim; it is correct and already exercised by
`test_coordinator_sweeps.py`. Stamp the token cost into each entry header the
way `append_digest` does (`coordinator_sweeps.py:71-72`) — that number is what
tells a user whether their bot's context is healthy.

**Keep `compact.py` bot-agnostic.** Do not import `ProjectStore` into it; 191
test files depend on it being pure history machinery. Add an optional callback:

```python
# compact.py:604 — new kwarg, default None
on_summary: Callable[[str, CompactAndSummarizeReport], None] | None = None
```

Call it right after `persist_session_summary` (`:725-727`) inside a
`try/except`, and wire the bot lookup at the call site in
`agent/loop/compactor.py` (`NexusCompactor`), which already has session
context.

**One rolling summary in the prompt, an unbounded-but-trimmed journal on
disk.** Do not put N dated checkpoints in the prompt — a months-long thread
accumulates them without bound and `usable_tokens()` is exactly what you would
be eating. Same prompt-vs-vault split `coordinator_sweeps` already uses
(`load_last_digest()` for the prompt, the 50-entry log for the record).

### `history_recall`: the dedupe cap becomes harmful

In `_handle_search` (`tools/history_recall_tool.py:235-245`) the per-session
cap is `max(2, limit // 2)` — with the default `limit=8` that is **4 hits per
session**. Once nearly every relevant hit shares one session id, a search that
should return 8 good hits from the bot's own thread returns 4 plus 4 pieces of
noise from unrelated sessions. The tool's headline capability degrades the day
single-thread ships.

1. **Exempt the current session from the cap** — apply it only to *other*
   sessions:
   ```python
   for r in merged:
       if r["session_id"] != sid:
           per_session = seen_sessions.get(r["session_id"], 0)
           if per_session >= max(2, limit // 2):
               continue
           seen_sessions[r["session_id"]] = per_session + 1
       r.pop("score", None)
       results.append(r)
       if len(results) >= limit:
           break
   ```
2. Extend `scope` (the enum is in the `ToolSpec` at `:77`) from `all|session`
   to `all | thread | bot`, keeping `session` as an accepted **alias** for
   `thread` — the model has been trained on the old value by the existing
   prompt text. `bot` = current sid ∪ every sid sharing its `project_id`; the
   query shape already exists in `store.list_activity`. `bot` is the genuinely
   new and useful scope: "everything this bot has ever done, including its
   background work."
3. Note `scope=all` does **not** fully collapse — hidden subagent/workflow
   sessions, archived pre-migration chats and the coordinator master all remain
   distinct sids.

### Leave the watermark/calibration LRU alone

`Agent._soft_compact_watermark` and `_token_calibration` are LRU-evicted at
`_WATERMARK_MAX_ENTRIES = 256`. It is tempting to re-key them by bot; don't.
The bound exists to stop unbounded growth across *every* sid the daemon sees,
and single-thread does not shrink that population: subagents, workflow
`agent_session` steps, calendar fires, kanban lane runs and vault dispatches
each mint a fresh sid. Those short-lived ids are exactly what the bound
protects against. It will now rarely *trigger* on bot threads — the desired
behaviour.

What *does* now matter more: both dicts are in-memory on the `Agent` singleton,
so a daemon restart loses the watermark and the calibration factor for a
months-long thread (previously it cost one over-eager compaction of a short
chat). **Follow-up, not blocking:** persist `_token_calibration` per session.

### On "checkpointed compaction" for cache reuse — rejected

An earlier draft proposed an append-only ledger of dated summaries *because* an
MLX prefix KV cache is invalidated by every prefix rewrite. That reasoning does
not hold: `compact_and_summarize` returns `[summary_msg] + recent` where
`recent` is a **scattered** survivor set chosen by `retention.partition`, so
the head differs after compaction regardless of how the summary is shaped. A
ledger changes what the prefix says, not that it changed.

Making compaction prefix-preserving would require it to be suffix-only — never
touching anything already prefilled — which is incompatible with summarizing
old turns, since old turns *are* the prefix. There is no clever middle.

**Context management wins, unambiguously.** (a) Losing a user's turn to
overflow is a correctness failure; a cache miss is a latency cost. (b)
`compact.py` + `retention.py` + `summarize.py` + `relevance.py` +
`parts_index.py` are the most carefully built part of this codebase, and
distorting them for a hardware optimisation is a bad trade. (c) The cache win
that actually matters — intra-turn reuse across tool-loop iterations
([`ws1`](ws1-mlx-provider.md) §S1-4) — is **unaffected by compaction**, because
compaction runs between turns, not between iterations.

## Build order

`S2-A..D` is the smallest shippable slice; the feature lands at **D**.

```
S2-A  schema.py + project_store.py: column, dataclass field, guarded read,
      set_main_session / main_session_id
S2-B  store.main_session_for_bot  (self-healing)
S2-C  migrate_single_thread.adopt_main_threads, called from init_fts
S2-D  chat.py:333 + chat_stream.py:107 routing      <-- feature lands here
S2-E  fork_session unregistration (+ "not advertised" test)
S2-F  thread_journal.py + compact.on_summary kwarg + compactor.py wiring
S2-G  history_recall: cap exemption + scope=all|thread|bot
S2-H  telegram /new, /switch, /chats repointing
```

E depends on D. F, G, H are parallel after D.

## Test impact

Most of the suite should pass unchanged. These are the ones that won't:

| File | Expected change |
|---|---|
| `test_fork_session.py` (6) | unchanged if the handler stays as unregistered code; **add** `test_fork_session_not_advertised` |
| `test_dispatch_visibility.py` | `test_chat_dispatch_stays_visible` is at risk — if a chat-originated dispatch goes through `get_or_create(None, project_id=…)` it now lands in the bot's main thread; the assertion becomes "appears as the bot's main thread" |
| `test_bots.py` | **new**: `main_session_id` round-trip, `main_session_for_bot` idempotence, self-heal after `delete(sid)` |
| `test_session_store_migration.py` | **new, highest-value test here**: adopts the most-recent visible non-team session, archives siblings, leaves `project_id IS NULL` alone, idempotent across two calls |
| `test_history_recall.py` (11) | `test_scope_session_filters_other_sessions` passes via the alias; `test_search_finds_live_history_across_sessions` may reorder once the cap is exempted — **assert membership, not order**. Add the two new tests above. |
| `test_compact_*.py` (4) | pass unchanged (`on_summary` defaults `None`); add `test_thread_journal_entry_written_on_compaction`, `test_journal_trims_to_max_entries` |
| `test_carried_context.py` | **watch this one** — it covers `is_carried_context` + the SYSTEM-stripping path, and it is the test that tells you whether the optional ws1 S1-H volatile-zone relocation is correct |
| `test_telegram_web_sync.py` | likely breaker — almost certainly asserts a specific `active_session_id` after `/new` |
| `test_coordinator*.py` (3) | should pass; `test_coordinator_inspect` may assert session *counts*, which change post-migration — fix fixtures, not behaviour |
| `test_subagent_tool.py`, `test_vault_dashboard.py` | unchanged (explicit hidden sids) — but verify the dashboard's `chat_session_id` doesn't route through `get_or_create(None, project_id=…)` |
