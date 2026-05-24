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
