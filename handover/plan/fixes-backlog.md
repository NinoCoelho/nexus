# Standalone fixes backlog

**Audience: any developer looking for a self-contained task.** Twelve defects
found while planning the macOS port. Each is independent of the port, verified
against the source at `542bfb5`, and shippable on its own.

Two are already fixed (#2); the rest are open.

---

### 1. `[dream].schedule_cron` is dead config

**Where:** declared `config_schema.py:376`, read **nowhere** in `agent/src`.
The driver gates on a hard-coded `_MIN_INTERVAL_HOURS = 24` in
`heartbeat_drivers/dream_trigger/driver.py` instead.

**Impact:** a user who sets a dream schedule gets silence. This is a large part
of why the feature feels inert.

**Fix:** honour it. loom's `parse_schedule` already accepts 5-field cron with
an optional trailing IANA timezone. Folded into
[`ws3`](ws3-dream-context-curator.md), but fine to do alone.

---

### 2. Docs referencing a deleted module — **FIXED**

`agent/src/nexus/features.py` does not exist (the subscription feature-gating
system was removed in `b4f6779`), yet `docs/features.md` documented it as the
live gating model and CLAUDE.md's last convention line told contributors to
edit it. Both corrected; the still-accurate iOS UX snapshot in
`docs/features.md` was kept.

Watch for other stale references to `FEATURE_TOOLS`, `FEATURE_ROUTES` or
`_should_register`.

---

### 3. Dream's session-count gate measures churn, not need

**Where:** `heartbeat_drivers/dream_trigger/driver.py::_count_recent_sessions`

**Impact:** it counts **all** rows in `sessions` updated since the last run,
including hidden subagent, workflow, calendar and dispatch sessions. So
`min_sessions_since_last` (default 5) is satisfied by background churn that has
nothing to do with whether there is anything worth consolidating. Under
[`ws2`](ws2-single-thread.md) it gets worse: with one visible thread per bot the
gate would be driven *entirely* by hidden runs.

**Fix:** gate on context pressure + idleness ([`ws3`](ws3-dream-context-curator.md) §3).

---

### 4. `ServerStore.client` allocates two `URLSession`s per access

**Where:** `ios/Nexus/Core/ServerStore.swift` — `client` is a computed property
that constructs a fresh `NexusClient` on **every** read, and `NexusClient.init`
builds both a REST session and a streaming session.

**Fix:** memoize per `activeId`. Do it during the `NexusKit` extraction
([`ws4`](ws4-native-ui.md) §1).

---

### 5. Managed-Chrome `main` context can hijack the user's own Chrome

**Where:** `browser/manager.py` — `main` is hard-pinned to `base_port` 9223
(`:1409-1410`, `:1581` "main's port is fixed, not allocated") and never goes
through `_alloc_port`.

**Impact:** if the user has their own Chrome running with
`--remote-debugging-port=9223`, Nexus connects to it and silently drives the
user's real browser — with their real cookies and sessions.

**Fix:** probe-and-relocate for `main` too. Note why the current code is
careful: the port allocation is persisted to `instances.json` **before**
launching, because a launch that outlives the in-turn wait must land on the
same port/profile pair on retry (a fresh port execs a second Chrome against the
same `--user-data-dir`, which the profile singleton absorbs, and the boot loops
forever). Preserve that invariant.

---

### 6. `browser` screenshots return a path, not an image part

**Where:** `browser/manager.py::_act_screenshot`; named as the v1 limitation in
`browser-computer-use-spec.md` §7.

**Impact:** the tool writes a PNG to disk and returns `{"screenshot": "<abs
path>"}`. A vision model therefore "sees" a filename. There is no working
computer-use vision loop today.

**Fix:** return the PNG as a multimodal `ContentPart` on the tool result
message. `ContentPart(kind="image", vault_path=…)` already exists and providers
base64-encode at request time, so the plumbing is there. **Highest-value item
in this list.**

---

### 7. NBIE telemetry dies with the process

**Where:** `browser/manager.py:1338` — `nbie_events: deque(maxlen=2000)`,
in-process only, documented as "a live dashboard, not an audit log".

**Impact:** the read cache-hit rate is the one number that says whether page
perception is wasting tokens (the spec records reads blowing the 15k per-turn
tool budget 5× in production). It is unrecoverable after a restart.

**Fix:** persist to SQLite. Keep the ring buffer as the hot path;
`aggregate_nbie_events` already produces the shape to store.

---

### 8. iOS silently drops four classes of stream event

**Where:** `ios/Nexus/Core/Models.swift:455` —
`ChatStreamEvent.decode(event:data:)` handles 13 named events; everything else
becomes `.unknown`.

**Missing:** `paused_for_cooldown`, `turn_started`, `team_cascade_capped`, and
the whole `subagent_*` family.

**Impact:** a cooldown pause or a turn started from another surface shows no UI
state on iOS.

**Fix:** in the shared core during the `NexusKit` extraction, so both clients
benefit.

---

### 9. iOS strands parked HITL prompts

**Where:** `ios/Nexus/Core/NexusClient.swift` implements `respond(...)` but not
the web client's 409-parked resume path (`POST /chat/{sid}/hitl/{rid}/answer`).

**Impact:** `POST …/respond` answers a *live* prompt. Once the turn has parked,
that endpoint returns 409 and the web client re-POSTs to the resume endpoint,
which streams the continuation. iOS does not, so answering a parked prompt from
the phone leaves the turn stuck.

**Fix:** mirror the web flow (404 = stale no-op, 409 = re-POST to
`/hitl/{rid}/answer` and consume the stream).

---

### 10. ~1,940 LOC of dead UI code

See [`ws5`](ws5-prune-and-integrate.md) §1 for the verified list
(`CoordinatorBubble/`, `useCoordinatorBubbleStatus.ts`, `InsightsView.css`).
Gate: `npm run build` must stay clean.

---

### 11. The agent's durable memory lives in a hidden dotfile

**Where:** `summarize.persist_session_summary` writes
`~/.nexus/vault/.session-memory/{session_id}.md`.

**Impact:** the agent's rolling understanding of a conversation — the thing
that determines whether it "loses track" — is in a dot-directory the user never
sees and cannot correct.

**Fix:** **not** by making that file editable. It is a `write_text` full
overwrite on every compaction, so a user edit would be silently destroyed,
usually within the hour. The right answer is the three-file split in
[`ws2`](ws2-single-thread.md) §"Never losing track": leave `{sid}.md` machine-
owned, add an append-only `thread-journal.md` per bot, and point users at the
already-existing editable `vault/{bot}/` + `memory_tool` surface.

---

### 12. Browser flows are locked to the surface that recorded them

**Where:** `browser/flows.py`; `browser/tool.py:396` refuses to replay a flow
whose `surface` (`"cdp"` vs `"ext"`) differs from the current one.

**Impact:** a flow recorded against the managed Chrome cannot be replayed on
the user's real tab, even though both surfaces run the **same** probe library
(`probes.py` / `probes.js`, kept byte-identical by a test).

**Fix:** make flows portable where the action sets overlap; keep the refusal
only for actions that genuinely exist on one surface only (e.g. `switch_tab`,
`fill_login`). Note the deliberate design that must survive: typed values are
never persisted — a `type` step is saved `needs_value: true` and replay aborts
there rather than replaying a password.

---

## Suggested grouping

| Batch | Items | Rationale |
|---|---|---|
| With ws3 | 1, 3, 11 | all dream/context, same files |
| With ws4 §1 | 4, 8, 9 | all fall out of the `NexusKit` extraction |
| With ws4 §5 | 5, 6, 7, 12 | all browser; 6 first, it's the biggest win |
| With ws5 | 10 | deletion batch, gated by `npm run build` |
