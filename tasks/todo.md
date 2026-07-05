# Performance & Enhancement Review — Implementation Plan

## Root cause of chat typing lag

Controlled textarea lifts every keystroke into the top-level `chatStates` Map
(`useChatSession.ts:80-87,106`), re-rendering the entire app tree. With zero
`React.memo` on chat components, `react-markdown` re-parses **every visible
assistant message on every keystroke** — the dominant CPU cost.

## Phase 1 — Frontend lag fixes (highest leverage, low risk)

- [x] F1. `React.memo` on `AssistantMessage` + stabilize feedback/pin callbacks
      (pass `msgIndex`, hoist App inline arrows to `useCallback`). Kills
      per-keystroke markdown re-parsing. **(biggest win)**
- [x] F2. `InputBar`: dedup `adjust()` (drop synchronous call, keep `useEffect`
      only) — removes one forced layout per keystroke.
- [x] F3. `InputBar`: drop redundant mention/secret detection in `onKeyUp`
      (keep `onClick` only) — removes extra `setState` per keystroke.
- [ ] F4. `React.memo` on `Sidebar` — **deferred**: Sidebar takes ~10 inline-arrow
      props in `App.tsx` (onViewChange, onMobileClose, onOpenSettings, …) that
      change identity every render; memo won't short-circuit until those are
      hoisted to `useCallback`. Do as a focused follow-up.
- [ ] F5. `React.memo` on view-pane components rendered alongside chat.
- [ ] F6. Move textarea draft to local component state; sync to `chatStates`
      only on send/blur (removes keystroke from global state entirely).

## Phase 2 — Backend lag fixes (event-loop blocking)

- [ ] B1. `BrokerPoller._discover_endpoints` vault `os.walk` → `asyncio.to_thread`.
- [ ] B2. Exempt `text/event-stream` from `FeatureGateMiddleware`/`SecurityHeadersMiddleware`
      (or convert to pure ASGI).
- [ ] B3. Drop eager `replace_history` at `chat_stream.py:315`; batch inserts.
- [ ] B4. `calendar_trigger`/`dream_trigger` driver `check()` vault+SQLite I/O → `to_thread`.
- [ ] B5. mtime-cached `load_config()` (currently 5 disk reads+parses per chat POST).

## Phase 3 — Memory / unbounded growth

- [ ] M1. LRU cap on frontend `chatStates` Map (evict least-recent non-thinking session).
- [ ] M2. `maxsize` on SSE subscriber queues (`pubsub.py:535,562`) + drop-on-full.
- [ ] M3. `dream_runs` retention: add `cleanup_old_runs` + wire to dream heartbeat tick.
- [ ] M4. Wire `cleanup_territory()` into dream engine run completion.
- [ ] M5. Wire `check_message_count` into agent loop pre-flight (dead code at `overflow.py:34`).
- [ ] M6. Daemon log rotation (`RotatingFileHandler`) in `daemon/manager.py:160`.
- [ ] M7. Startup sweep of `~/.nexus/tmp/` (decouple from in-memory import dict).
- [ ] M8. `llm_errors` retention at startup (e.g. 90-day delete).

## Phase 4 — Background process robustness

- [ ] P1. Dream `is_running` stale-lock reconciliation at startup
      (mirror workflow `reconcile_stale_runs`).
- [ ] P2. Re-register fs_watch/rss/event triggers in `workflows.init()` on restart.
- [ ] P3. MCP server bridge thread: store handle on `app.state`, shutdown hook in lifespan.
- [ ] P4. Prune fs_watch `_pending` debounce dict by age.
- [ ] P5. Close OCR `log_handle` in parent after `Popen`.
- [ ] P6. Skip dream_trigger DB open + config load when dream disabled.

## Verification

After each phase: `npm run build` (tsc) from `ui/`, `uv run ruff check` + `uv run pytest`
from `agent/`.

## Summary

Review synthesized from 4 parallel specialist investigations (frontend lag,
backend lag, background processes, memory protection). Phase 1 in progress.
