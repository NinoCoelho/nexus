# Feature notes

> **The subscription feature-flag system this file used to describe is gone.**
> `agent/src/nexus/features.py`, `FeatureGateMiddleware` and the
> `FEATURE_TOOLS` / `FEATURE_ROUTES` maps were removed in commit `b4f6779`:
> every tool and route now registers unconditionally, and server auth is
> loopback-or-token with no per-feature gating. The later
> `ui_mode: normal|advanced` view-hiding was also removed — advanced views
> live behind `#/advanced/*` routes instead. The `ui_mode` field survives in
> `server/settings.py` for compatibility, but nothing reads it.
>
> Do not add feature gating by editing `features.py` — it does not exist.

## Native iOS Client (chat UX state)

Snapshot of the native SwiftUI app (`ios/`, see `ios/README.md`) as of the
UX phases 0–1 — everything below is verified against the source.

### Implemented

**Transcript & scrolling (ChatView)**
- Non-lazy full `VStack` transcript — every row is realized before the
  viewport lands, so scroll offsets are computed against true heights.
- Open-at-bottom without flashes: the content is hidden behind a
  `ProgressView` until the initial batch is laid out and the viewport has
  scrolled explicitly to the newest message.
- Dynamic auto-scroll via a sentinel `GeometryReader` + `PreferenceKey`
  (`ChatBottomKey`): the view follows streaming only while the user is at
  the bottom (≤ 64 pt); scrolling up releases it. Re-lands on the bottom
  after the keyboard shows.
- Floating ⌄ "jump to latest" button whenever the viewport is away from
  the transcript's bottom.

**Message interactions**
- Copy any message via `contextMenu` long-press, with a success haptic
  (`UINotificationFeedbackGenerator`).
- Dismissible error toast (`ErrorBanner(onDismiss:)`) shown as a top
  overlay — network/stream/upload errors surface even when history is
  already on screen, not only on a blank transcript.

**In-chat search**
- Toolbar 🔍 opens a search bar over the in-memory transcript.
- Local filter, case- and diacritic-insensitive (`.range(of:options:)`).
- Hit counter (`3/12`), prev/next arrows with `scrollTo` (anchor: center),
  wrap-around; newest match on query change.
- Term highlighted inside bubbles via `AttributedString` background color
  (`MarkdownText.applyingHighlight`) — both plain (user) and markdown
  (assistant) bubbles.

**Composer & clipboard**
- Paste is offered whenever the clipboard has strings *or* an attachment
  (`hasStrings` metadata check avoids the paste-permission prompt on
  long-press).
- Mixed clipboard (image + text): the image is staged as an attachment at
  0.85 JPEG quality *and* its text is inserted into the draft.
- Screenshot / video / Files-app copies route to attachment staging;
  staged files upload to the vault and are referenced by path.

**Client hygiene (NexusClient)**
- `hitlCancel(sessionId:requestId:)` — POST `/chat/{sid}/hitl/{request_id}/cancel`.
- `archiveSession(id:)` — PATCH `/sessions/{id}/archive`.
  Both replace hand-rolled `URLSession` calls that duplicated the Bearer
  header and swallowed transport/auth errors.

### Roadmap (planned, not implemented)

- **Rich content-part rendering** — inline images / PDFs / audio in chat
  bubbles (multi-part messages currently render as `📎 <path>` text lines).
- **Voice** — speech input and spoken message playback.
