# Feature Flag System — Implementation Plan

## Key Decisions

| Decision | Answer |
|---|---|
| Self-hosted | Not supported — subscription is mandatory |
| Feature source | Exclusively from nexus-llm plan |
| `cloud_models` | Subscription-only, no config override |
| Disabled feature data | Preserved on disk, visible in vault tree, but views/tools/APIs return errors |
| UI mode | Per-user, stored in `settings.json` |
| First-run | Must force Nexus account sign-in before app is usable |
| Insights | Completely removed |

## Feature Taxonomy & Tool Mapping

| Feature Key | Agent Tools | UI Views | Backend Routes |
|---|---|---|---|
| `chat` | — (core) | `chat` | `/chat/*` |
| `local_models` | — (provider config) | — | `/local/*` |
| `cloud_models` | `web_search`, `web_scrape` (indirect) | Provider wizard cloud entries | `/providers/*/key` |
| `kanban` | `kanban_manage`, `kanban_query`, `show_kanban` | `kanban` | `/vault/kanban/*` |
| `calendar` | `calendar_manage` | `calendar` | `/vault/calendar/*`, `/alarms/*` |
| `database` | `datatable_manage`, `dashboard_manage`, `vault_csv`, `visualize_table`, `show_data_table`, `show_dashboard_widget` | `data` | `/vault/datatable/*`, `/vault/dashboard/*` |
| `workflow` | — (HTTP API) | `workflows` | `/workflows/*`, `/workflow/*` |
| `knowledge` | `vault_semantic_search`, `ontology_manage` | `graph` | `/graph/*`, `/graphrag/*` |
| `dream` | — (background engine) | `dream` | `/dream/*` |
| `heartbeat` | `manage_heartbeat`, `dispatch_card` | `heartbeat` | `/heartbeat/*` |
| `multi_user` | — | Login/register UI | `/auth/*`, `/admin/*`, `/share/*` |

**Always-on tools:** `skills_list`, `skill_view`, `skill_manage`, all `vault_*` (except semantic_search), `memory_read/write`, `http_call`, `ask_user`, `terminal`, `notify_user`, `spawn_subagents`, `context_status`, `fork_session`, `nexus_kb_search`, `ocr_image`.

**Normal mode hides:** `knowledge` (graph), `heartbeat`, `dream`.

## Plan Assignments

| Plan | Features |
|---|---|
| **Free** | `chat`, `local_models` |
| **Starter** | `chat`, `local_models`, `cloud_models`, `kanban`, `calendar`, `database` |
| **Pro** | `chat`, `local_models`, `cloud_models`, `kanban`, `calendar`, `database`, `workflow`, `knowledge`, `dream`, `heartbeat`, `multi_user` |

## API Changes

| Endpoint | Change |
|---|---|
| nexus-llm `GET /api/status` | Add `features: string[]` |
| Nexus `GET /config` | Add `features: { active: string[], all: string[] }` |
| Nexus `GET /settings` | Add `ui_mode` |
| Nexus `POST /settings` | Accept `ui_mode` |
| Nexus `GET /auth/nexus/status` | Add `features: string[]` |
| Nexus disabled-feature routes | Return `403` |
| Nexus `GET /insights` | Removed |

## Implementation Order

- [ ] P1: Remove insights completely
- [ ] P2: nexus-llm — add features to status + seed plans + admin editor
- [ ] P3: Backend — features.py + propagation from status watcher
- [ ] P4: Backend — dynamic tool registration in registry.py
- [ ] P5: Backend — route gating middleware
- [ ] P6: Backend — settings ui_mode
- [ ] P7: Frontend — feature context + sidebar filtering
- [ ] P8: Frontend — first-run mandatory login flow
- [ ] P9: Frontend — provider wizard cloud gating
