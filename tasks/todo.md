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
- [x] F4. `React.memo` on `Sidebar` + hoist its ~10 inline-arrow props in App to
      `useCallback` (onViewChange, onMobileClose, onOpenSettings, …).
- [ ] F5. `React.memo` on view-pane components rendered alongside chat.
- [x] F6. Move textarea draft to local component state (`InputBar`); push to
      `chatStates` on a 300ms debounce + on send (pass `draft` as the onSend
      override so parent never reads stale `state.input`). Resets on session
      switch via the `onChange`-identity dep.

## Phase 2 — Backend lag fixes (event-loop blocking)

- [x] B1. `BrokerPoller._discover_endpoints` vault `os.walk` → `asyncio.to_thread`
      (both the per-cycle `_poll_all` and startup `_ensure_webhook_tokens`).
- [ ] B2. Exempt `text/event-stream` from `FeatureGateMiddleware`/`SecurityHeadersMiddleware`
      (or convert to pure ASGI).
- [ ] B3. Drop eager `replace_history` at `chat_stream.py:315` — **deferred**: the
      finally-clause persist covers it, but dropping it regresses reload-during-turn
      UX (user msg not in DB until turn ends). Needs a cheaper append or to_thread.
- [x] B4. `calendar_trigger` driver `check()`: pre-load all calendars
      (list_calendars + read_calendar vault walk) in `asyncio.to_thread`, and
      run `alarm_store.garbage_collect` in a thread. dream_trigger's remaining
      sync ops are lightweight SQLite reads (early-return in most ticks) — left
      as-is to avoid over-engineering.
- [x] B5. mtime-cached `load_cached()` in config_file.py; migrated chat_stream,
      broker poller, dream_trigger to it. `save()` invalidates the cache.

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
