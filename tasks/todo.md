# Nexus Uplift — Plan

Decisions (locked with user 2026-09-30):
- Main nav keeps Vault, Calendar, Kanban, Workflows (kanban restored on user request).
- Coordinator: ONE master chat, proactive from day one (heartbeat sweeps + Telegram digests).
- Execution: big restructure first, then features phased on the new skeleton.
- Every nav view lists its items in the sidebar's lower panel; Chat lists only
  unprojected chats; Settings lives in the header gear.

## Phase 0 — Structural restructure ✅
- [x] Route model + URL routing (`ui/src/routes.ts`, `#/view[/path]`, `#/advanced/<area>`,
      legacy redirects, back/forward)
- [x] App.tsx decomposed into view panes (`components/views/`) with shared `views.css`
- [x] Nav: Chat · Projects · Apps · Vault · Calendar · Kanban · Workflows (+ Settings gear in header)
- [x] Sidebar list panels for every view (sessions/projects/apps/tree/calendars/boards/workflows)

## Phase 1 — Project workspace ✅
- [x] ProjectsPane workspace: metadata, vault link, TG binding chips, instructions, title+FTS search
- [x] Backend `GET /telegram/bindings` (+ PATCH/DELETE in Phase 5)

## Phase 2 — Apps as main-area surfaces ✅
- [x] AppsPane grid → dashboard → table drill-down → ER diagram; `#/apps/<folder>` deep links

## Phase 3 — Coordinator master chat ✅
- [x] `nexus_sessions` + `session_dispatch` tools (master-session gated)
- [x] `[coordinator]` config, auto-provisioned Master session, prompt block, Master badge
- [x] Telegram DM → master session; hot enable via PATCH /config
- [x] Proactive sweeps (read-only enforced, quiet hours, NOTHING_NEW, digest delivery)
- [x] 3b: dispatch approval tiers — `auto_approve` ("all" or project ids); unapproved
      dispatch refused with `needs_confirmation` → ask_user → retry `confirmed=true`

## Phase 4 — Advanced subsite ✅
- [x] `#/advanced/graph|heartbeat|dream` + Settings → Advanced tools cards
- [x] Tabbed AdvancedPane (route-driven, tabs mount lazily on first visit, stay alive)
- [x] ui_mode hiding retired (useFeatures deleted; server field kept for compat)

## Phase 5 — Telegram topic management UX ✅
- [x] HITL after /switch fixed (context-prefix fallback in find_by_session)
- [x] Bindings admin API + Settings "Linked chats & topics" table (rebind/unbind)
- [x] /topics command; /project inline buttons (already existed)

## Phase 6 — Cleanup, tests, docs ✅
- [x] Dead code removed (ProjectSection, old KanbanListPanel, useFeatures, AppsPane.css)
- [x] CLAUDE.md + tasks/todo.md updated to the new architecture
- [x] Full backend suite + ruff + tsc/vite green

## Review / Results (final)

All phases implemented, each committed separately (76197a3 → head). UX corrections
folded in along the way per user feedback: sidebar list panels everywhere, flat
unprojected chat list, Kanban restored as a view, settings gear in the header
(+ flex fix so the sidebar bottom never jumps), calendars panel instead of the
dropdown, shared views.css design language.

**How to test the coordinator**: Settings → Features → Coordinator → enable;
message the bot from your DM (becomes the master chat); ask it to "list my
projects" (nexus_sessions) or "ask project X to do Y" (session_dispatch — expect
an approval prompt unless auto_approve is set). Sweep: set interval to e.g. 5
minutes in Settings, wait for the digest.

**Known smaller items left intentionally**:
- `auto_approve` is config-file only (no UI editor) — ids are ugly; prompt-level
  confirmation covers the common case.
- Mobile tab bar has no Kanban/Calendar/Workflows tabs (drawer access only).
- Server `ui_mode` settings field remains for compat; nothing reads it.

### Lessons
- explore-agent claims about bugs can be wrong: the "session pagination bug" was actually
  correct semantics (`/sessions` returns ALL project sessions; X-Total-Count = ungrouped only).
  Verify endpoint behavior in source before "fixing" comparisons.
- Every nav view must list its items in the sidebar's lower panel (user correction): new views
  get a list panel, selection lifted to App — not main-area-only navigation.
- Chat list shows ONLY unprojected chats once Projects is a dedicated view (user correction).
- New surfaces must reuse the app's tokens (--border-soft for borders, not --bg-soft; var(--radius);
  --bg-hover hovers) and the shared views.css language — bespoke CSS looks unprofessional.
- Don't remove a top-level view (kanban) without giving its items an equally reachable home.
- Keep-mounted children must mount lazily (mount-on-first-activation), else lazy bundles all load.

---

# Voice Conversation Mode — Master Chat (2026-10-01)

Decisions (locked with user): web-UI overlay on master chat, full-duplex with
barge-in, local-only models (silero-vad + faster-whisper + Piper), full reply
sentence-streamed. Architecture: voicekit-style gateway (`WS /voice/stream`)
wrapping the existing agent loop via `turn_launcher.launch_turn` into
`coordinator.ensure_session()`; the gateway speaks, the agent loop is untouched.

## M1 — transport + half-duplex core ✅
- [x] `[voice]` config section (endpoint_ms, min_speech_ms, barge_in, barge_in_min_speech_ms, speak_max_words) in config_schema.py
- [x] `nexus/voice/` package: vad.py (silero-vad), endpoint.py (state machine), asr.py (reuse cached whisper), sentences.py (delta accumulator), tts_stream.py (per-sentence Piper + prefetch + cancel), gateway.py (WS /voice/stream)
- [x] Register WS route in server/app.py; LoopbackOrTokenMiddleware is BaseHTTPMiddleware → never sees WS scopes, gateway self-enforces loopback + refuses proxy headers
- [x] ui/src/voice/: VoiceSession.ts (WS client + blob-URL worklet + AEC capture + playback queue + barge-in stop), voiceActive.ts (ack suppression flag), useVoiceMode.ts
- [x] ui/src/components/VoiceOverlay/: full-screen overlay (state orb reacts to VAD level, transcript, mute/stop/end); entry = mic button in the CoordinatorBubble panel header
- [x] Suppress double speech: `suppress_voice_ack` on launch_turn/ChatTurnRunner + ack player skips while the overlay is active

## M2 — barge-in ✅
- [x] Server VAD barge-in + client instant stop (`barge_in` event) → pipeline cancel + speech suppression until the new input owns the turn
- [x] Echo guard: barge gate raises min-speech while audio streams out (browser AEC + 400 ms sustain)
- [x] Mid-turn interruption rides queue-then-inject; post-settle = plain new turn

## M3 — polish (partial)
- [x] HITL surfaced in overlay as a "answer it in the chat panel" note (spoken question + form UI = future)
- [ ] Settings → Features → Voice section (config-file only for now)
- [x] Verification: 20 unit/integration tests (endpoint machine, accumulator, pipeline, full WS loop vs real uvicorn + fake provider/ASR/TTS/VAD); ruff clean; npm run build clean; full suite 1468 passed
- [ ] Latency: endpoint ~600ms + whisper ~400ms + first token ~500ms + first sentence ~300ms ≈ 1.8s target

Risks: speaker echo (browser AEC + 400ms gate + headset advice, `barge_in=false`
fallback), Piper underrun (prefetch + 800-word cap), whisper-base pt accuracy
(transcript shown in overlay; model configurable in `[transcription]`).

Out of scope: wake word, Chrome panel voice, streaming partials (config stub
`partials=false`), voice through tunnel.


### Voice mode — implementation notes (2026-10-01)
- Full-duplex loop: mic PCM (16k int16) → silero VAD (onnxruntime direct, no
  torch — the pip `silero-vad` package hard-depends on torch; model ~2.2 MB
  auto-downloads to ~/.nexus/voice/models/) → EndpointDetector →
  faster-whisper → `launch_turn(is_voice=True, suppress_voice_ack=True)` →
  session-bus deltas → SentenceAccumulator → SpeechPipeline (per-sentence
  Piper, lazy worker, cancel/resume) → WAV frames back over the same WS.
- The gateway is the speaker: `suppress_voice_ack` keeps regular acks silent
  server-side; `voiceActive.ts` gates the web ack player client-side.
- After a barge-in the interrupted turn's remaining text is muted (shown,
  not spoken) until `user_injected` / `turn_settled` / the next utterance —
  `SpeechPipeline.resume()` + `_suppress_speech` in gateway.py.
- Playback drain is client-acknowledged (`playback_end`); orphaned acks fail
  open after 20 s so barge-in gating can't wedge.
- Client worklet is a Blob-URL AudioWorklet (no public/ asset), capture uses
  echoCancellation/noiseSuppression/autoGainControl, linear-resamples
  ctx-rate → 16k with carry for continuity; sends 1536-sample batches
  (3 exact VAD windows) per WS frame.
- **Bugfix (2026-10-01, user report)**: utterance audio was garbled — the
  gateway re-sliced WS messages instead of using the VAD's own window
  framing (leftover buffering desynced alignment every ~100 ms → Whisper
  hallucinated multilingual text). `SileroVAD.feed` now returns
  `(prob, window)` pairs; gateway captures those exact windows.
- **Bugfix round 2 (2026-10-01)**: still garbled → resampling moved off the
  browser. Client streams native-rate int16 (`?rate=`, AudioContext rate);
  server `voice/resample.py::StreamResampler` (linear, cross-block carry +
  phase) feeds the VAD. Worklet now pulls through a gain-0 sink. Validated
  with real speech (`say` fixture) end-to-end at 48 kHz through real VAD +
  real whisper: exact transcript.

---

# Fast conversation lane — master chat (2026-10-01, afternoon) — ROLLED BACK

**User rolled the entire voice-mode effort back** (both waves: gateway +
fast lane). All code reverted via git, config restored
(transcription base/auto, no [voice]/[coordinator] extras), ~/.nexus/voice
deleted, UI rebuilt. Kept below as a record of what was tried and why it
failed — see lessons.md (2026-10-01 entries) before retrying.

Decisions (locked): confirm only when ambiguous; voice first (text ⚡ button later);
STT small + language pt; rolling history trim for the coordinator.

Why: log showed every master turn shipping msgs=196 + tools=183 to the LLM —
first-token latency is dominated by prompt weight. Fast lane = tiny context,
no tools, direct answers / one clarifying question / quick DDGS search /
escalate to the agent loop with clarified context embedded.

## Phases
- [x] P0: config — [transcription] model=small language="pt"; [voice] endpoint_ms=450
- [x] P1: [voice] fast_lane + fast_model config (schema + PATCH allowlist)
- [x] P1: voice/fast_lane.py — marker protocol (ANSWER/CLARIFY/SEARCH/AGENT),
      rolling ~6-exchange transcript, agentive-regex pre-classifier,
      fast-model call mirroring voice_ack._generate_text
- [x] P1: gateway — fast paths only when idle (busy → queue-inject as today);
      answer/clarify (max 2 rounds)/search (loom DDGS + summary)/agent
      (handoff line + context block in launch_turn message); fast_reply +
      fast_done WS events; latency log (ms to first audio per path)
- [x] P1: overlay — render fast_reply sentences; fast_done closes the turn
- [x] P4: [coordinator] max_history_turns (0=off) — trim in launch_turn at
      user-message boundaries, never orphaning tool results
- [x] Tests + ruff + build + daemon restart — 26 voice tests, 1478 full suite, build clean; live config: transcription small+pt, voice endpoint_ms=450, coordinator max_history_turns=40
