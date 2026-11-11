# Feature Flags

Nexus uses a **subscription-gated feature flag system**. Every user must have a Nexus account (via nexus-llm). The features available to a user are determined by their subscription plan.

## How It Works

1. **nexus-llm** stores feature lists per plan in Firestore.
2. The `/api/status` endpoint returns the plan's `features` array alongside usage/billing data.
3. The Nexus desktop app polls status and caches the active feature set.
4. Backend uses the feature set to gate tool registration, route access, and background engines.
5. Frontend uses the feature set to show/hide sidebar views and wizard options.
6. A per-user **UI mode** (`normal` / `advanced`) additionally controls sidebar density.

## Feature Keys

| Key | Description | Tools Gated |
|---|---|---|
| `chat` | Basic chat functionality (always on) | — |
| `local_models` | Local model providers (Ollama, llama-server) | — |
| `cloud_models` | Cloud/external LLM providers (OpenAI, Anthropic, etc.) | `web_search`, `web_scrape` |
| `kanban` | Kanban boards | `kanban_manage`, `kanban_query`, `show_kanban` |
| `calendar` | Calendar events & alarms | `calendar_manage` |
| `workflow` | Visual workflow engine | — (HTTP API only) |
| `knowledge` | GraphRAG / knowledge graph | `vault_semantic_search`, `ontology_manage` |
| `dream` | Background dream engine | — (background) |
| `heartbeat` | Heartbeat scheduler | `manage_heartbeat`, `dispatch_card` |
| `multi_user` | Multi-user server mode | — |
| `database` | Data tables & dashboards (DuckDB-backed) | `datatable_manage`, `dashboard_manage`, `vault_csv`, `visualize_table`, `show_data_table`, `show_dashboard_widget` |

## UI Mode

- **Normal**: Hides `knowledge` (graph), `heartbeat`, `dream` from sidebar.
- **Advanced**: Shows all active-feature views.
- Stored per-user in `~/.nexus/settings.json` as `ui_mode`.

## Disabled Features

When a feature is disabled:
- **Data is preserved** on disk (kanban boards, workflow definitions, etc.)
- **Vault tree** still shows the `.md` files
- **Views** are hidden from sidebar; direct navigation redirects to `#/chat`
- **API routes** return `403 {"detail": "Feature 'X' is not available on your plan"}`
- **Agent tools** are unregistered (agent cannot invoke them)

## Plan → Feature Mapping

| Feature | Free | Starter | Pro |
|---|---|---|---|
| `chat` | ✓ | ✓ | ✓ |
| `local_models` | ✓ | ✓ | ✓ |
| `cloud_models` | | ✓ | ✓ |
| `kanban` | | ✓ | ✓ |
| `calendar` | | ✓ | ✓ |
| `database` | | ✓ | ✓ |
| `workflow` | | | ✓ |
| `knowledge` | | | ✓ |
| `dream` | | | ✓ |
| `heartbeat` | | | ✓ |
| `multi_user` | | | ✓ |

## Backend Integration

### Feature Resolution (`agent/src/nexus/features.py`)

```python
from nexus.features import get_features, set_features, ALL_FEATURES
```

- `set_features(features: set[str]) -> bool` — update cache, returns True if changed
- `get_features() -> set[str]` — current active feature set

### Tool Registration

`build_tool_registry()` in `registry.py` accepts active features. Tools are grouped by feature key via `FEATURE_TOOLS` mapping. Only tools for active features are registered.

### Route Gating

Middleware checks path prefixes against `FEATURE_ROUTES` mapping. Disabled features return `403`.

### Status Propagation

- `nexus_account.py` extracts `features` from `/api/status` response
- `status_watcher.py` calls `features.set_features()` on change
- Emits `features_changed` SSE event

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
