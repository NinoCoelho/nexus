# Nexus Multi-Tenant Plan: Nexus Lite & Enterprise

**Date**: 2026-06-01
**Status**: Planning — not yet implemented
**Scope**: Create multi-tenant SaaS (Lite) and on-premise enterprise (Enterprise) offerings, sharing the existing Nexus codebase via a separate repository.

---

## Table of Contents

1. [Current State Summary](#current-state-summary)
2. [Architecture Overview](#architecture-overview)
3. [Repository & Package Structure](#repository--package-structure)
4. [Feature Matrix](#feature-matrix)
5. [Sandboxing Strategy](#sandboxing-strategy-for-lite)
6. [Database Strategy: PostgreSQL](#database-strategy-postgresql)
7. [Object Storage: S3/MinIO](#object-storage-s3minio)
8. [Authentication Architecture](#authentication-architecture)
9. [LLM Gateway (Lite)](#llm-gateway-lite)
10. [Billing & Subscriptions](#billing--subscriptions)
11. [Enterprise On-Premise Deployment](#enterprise-on-premise-deployment)
12. [Implementation Phases](#implementation-phases)
13. [Technical Risks & Mitigations](#technical-risks--mitigations)
14. [What This Preserves](#what-this-preserves)
15. [Open Items for Discussion](#open-items-for-discussion)

---

## Current State Summary

After deep analysis of the codebase:

- **12+ SQLite databases** (raw SQL, no ORM), all under `~/.nexus/`
  - `sessions.sqlite` (Loom SessionStore + Nexus FTS5, HITL, feedback tables)
  - `dream_state.sqlite` (dream engine runs, budget, explored territory)
  - `workflow_runs.sqlite` (workflow execution history, webhook tokens)
  - `vault_index.sqlite` (FTS5 full-text search over vault)
  - `vault_meta.sqlite` (tags, backlinks, file metadata)
  - `heartbeat.db` (heartbeat state, alarm state, fire audit)
  - `server.sqlite` (multi-user: users, invites, ACL)
  - `broker_webhooks.db` (webhook registry)
  - `memory.sqlite` (Loom memory store index)
  - `graphrag_manifest.sqlite` (content-hash manifest)
  - Per-folder `.nexus-graph/manifest.sqlite` (graph index)
  - Per-folder GraphRAG SQLite files (chunks, entities, vectors)

- **40+ filesystem paths** for vault, skills, config, TTS models, binaries, temp files, etc.

- **Multi-user mode already exists**: JWT (HS256), role-based access (admin/member/viewer), per-user directories under `~/.nexus/users/<id>/`, ACL-based vault sharing, invite system, admin panel. But it's all SQLite + filesystem-backed.

- **Feature flags** already subscription-based via `nexus-model.us/api/status` — returns `features[]` from the user's plan. Currently: `chat`, `local_models`, `cloud_models`, `kanban`, `calendar`, `workflow`, `knowledge`, `dream`, `heartbeat`, `multi_user`, `database`, `projects`.

- **Loom dependency** has partial Protocol abstractions (`VaultProvider`, `StorageBackend`, `EmbeddingProvider`, `SecretStoreProtocol`) but core stores (`SessionStore`, `VectorStore`, `GraphRAGEngine`, `HeartbeatStore`) are SQLite-locked concrete classes.

- **One global Agent instance** per server — not per-session or per-user. State is scoped via `ContextVar`s (`CURRENT_SESSION_ID`, `ALLOWED_TOOLS`, etc.).

- **Frontend**: React 19 + Vite, no router library (hash-based view switching), state-driven. Auth already handles tunnel login, multi-user login, and Nexus account sign-in via `AuthGate` component.

### Key Files Reference

| Area | Key Files |
|---|---|
| Server assembly | `server/app.py` (create_app, all middleware, routers) |
| Server lifecycle | `server/app_lifespan.py` (startup/shutdown) |
| Auth middleware | `server/app.py` (LoopbackOrTokenMiddleware), `server/middleware.py` (MultiUserAuthMiddleware) |
| Auth manager | `server/auth.py` (JWT, CurrentUser deps) |
| Feature flags | `features.py` (ALL_FEATURES, FEATURE_TOOLS, FEATURE_ROUTES, set_features, is_enabled) |
| Status watcher | `auth/status_watcher.py` (polls nexus-model.us, applies feature changes) |
| Nexus account | `auth/nexus_account.py` (Firebase exchange, status fetch) |
| Session store | `server/session_store/store.py` (wraps LoomSessionStore) |
| Session registry | `server/session_store/registry.py` (per-user stores in multi-user) |
| User store | `server/user_store/store.py` (users, invites, ACL) |
| Permissions | `server/permissions.py` (role → tool allowlists) |
| Home paths | `home.py` (~/.nexus/ path resolution, ContextVar-aware) |
| Config | `config_file.py`, `config_schema.py` |
| Secrets | `secrets.py` (TOML at ~/.nexus/secrets.toml) |
| Store factory | N/A — stores created inline in main.py and app.py |
| Vault | `vault.py`, `vault_index.py`, `vault_search.py`, `vault_graph.py` |
| Skills | `skills/` (registry, guard, venv_manager) |
| Agent loop | `agent/loop/` (tool-calling loop over LLM provider) |
| Tool registry | `agent/_loom_bridge/registry.py` (_should_register gates by feature) |
| Workflow store | `workflows/store.py` (manual SQLite) |
| Dream store | `dream/state.py` (extends SqliteStore base) |
| Alarm store | `alarm_store.py` (extends SqliteStore base) |
| CLI | `cli/__init__.py` (Typer app), `cli/serve.py` (server bootstrap) |
| Main entry | `main.py` (build_app, creates global Agent + FastAPI app) |

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                    nexus-cloud (NEW REPO)                 │
│   Multi-tenant platform. Imports nexus as a library.     │
│                                                           │
│   ┌─────────────┐  ┌─────────────┐  ┌────────────────┐  │
│   │ Postgres    │  │ S3/MinIO    │  │ Auth adapters   │  │
│   │ backends    │  │ file store  │  │ (SSO/LDAP/OIDC) │  │
│   └──────┬──────┘  └──────┬──────┘  └───────┬────────┘  │
│          │                │                  │            │
│   ┌──────┴────────────────┴──────────────────┴────────┐  │
│   │            Nexus Cloud App (FastAPI overlay)       │  │
│   │   Tenant management, billing, admin dashboard      │  │
│   │   Usage tracking, rate limiting, sandboxing        │  │
│   └──────────────────────┬─────────────────────────────┘  │
│                          │ imports                         │
│   ┌──────────────────────┴─────────────────────────────┐  │
│   │            nexus (CURRENT REPO, as package)         │  │
│   │   + Storage abstraction protocols (new)             │  │
│   │   + Swappable store factories (new)                 │  │
│   └─────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

### Dependency Graph

```
nexus-interfaces  ←  nexus  ←  nexus-cloud
                  ←  nexus-cloud
```

- `nexus-interfaces`: Pure Protocol/ABC definitions, no implementations, no heavy dependencies
- `nexus`: Current repo, depends on `loom` + `nexus-interfaces` (optional), SQLite by default
- `nexus-cloud`: Depends on `nexus` + `nexus-interfaces`, provides PostgreSQL/S3/auth implementations

---

## Repository & Package Structure

### nexus-interfaces (NEW REPO)

```
nexus-interfaces/
├── pyproject.toml
└── src/nexus_interfaces/
    ├── __init__.py
    ├── store.py          # Core store protocols
    ├── file_storage.py   # File I/O abstraction
    ├── auth.py           # Auth provider protocol
    ├── config.py         # Config provider protocol
    ├── tenant.py         # Tenant data model
    └── sandbox.py        # Sandbox policy definition
```

Key protocols:

```python
# store.py
class SessionStoreProtocol(Protocol):
    async def get_or_create(self, session_id, context, project_id): ...
    async def replace_history(self, session_id, messages): ...
    async def get_history(self, session_id): ...
    async def list_sessions(self, ...): ...
    async def delete(self, session_id): ...
    async def publish(self, session_id, event): ...
    async def subscribe(self, session_id): ...
    # ... mirrors LoomSessionStore + Nexus additions

class WorkflowStoreProtocol(Protocol):
    async def create_run(self, ...): ...
    async def update_run(self, ...): ...

class VaultIndexProtocol(Protocol):
    async def index_file(self, path, content): ...
    async def remove_file(self, path): ...
    async def search(self, query, limit): ...
    async def get_tags(self): ...
    async def get_backlinks(self, path): ...

class DreamStoreProtocol(Protocol): ...
class AlarmStoreProtocol(Protocol): ...
class HeartbeatStoreProtocol(Protocol): ...
class UserStoreProtocol(Protocol): ...
```

```python
# file_storage.py
class FileStorageProtocol(Protocol):
    async def read(self, path: str) -> bytes: ...
    async def write(self, path: str, data: bytes) -> None: ...
    async def delete(self, path: str) -> None: ...
    async def list(self, prefix: str) -> list[FileInfo]: ...
    async def move(self, src: str, dst: str) -> None: ...
    async def exists(self, path: str) -> bool: ...
    async def size(self, path: str) -> int: ...
    async def get_presigned_url(self, path: str, expires: int) -> str: ...
```

```python
# auth.py
class AuthProviderProtocol(Protocol):
    async def authenticate(self, request: Request) -> AuthResult: ...
    async def get_user(self, user_id: str) -> User: ...
    async def create_session(self, user: User) -> str: ...  # JWT
    async def validate_session(self, token: str) -> User: ...
    async def revoke_session(self, token: str) -> None: ...
```

```python
# tenant.py
@dataclass
class Tenant:
    id: UUID
    slug: str
    plan: Literal["lite", "enterprise"]
    status: Literal["active", "suspended", "provisioning"]
    settings: dict
    quota: Quota
    created_at: datetime
```

```python
# sandbox.py
@dataclass
class SandboxPolicy:
    disabled_tools: set[str]
    max_file_size: int
    max_vault_size: int
    max_concurrent_sessions: int
    daily_turn_limit: int
    allowed_http_domains: set[str] | None  # None = all allowed
    allow_skill_creation: bool
    allow_workflow_exec: bool
```

### nexus (CURRENT REPO — minimal changes)

No structural changes. New files added:

```
agent/src/nexus/
├── store_factory.py     # NEW: factory to create stores based on backend
├── interfaces.py        # NEW: re-exports from nexus-interfaces (optional dep)
└── ...                  # Everything else unchanged
```

Modified files (minimal):
- `main.py` — use factory instead of direct store creation
- `home.py` — add `set_home_resolver()` hook for cloud
- `features.py` — add `"lite"` and `"enterprise"` to `ALL_FEATURES`
- `pyproject.toml` — add `nexus[cloud]` optional dep on `nexus-interfaces`

### nexus-cloud (NEW REPO)

```
nexus-cloud/
├── pyproject.toml                    # Depends on nexus + nexus-interfaces
├── src/nexus_cloud/
│   ├── __init__.py
│   ├── postgres/                     # PostgreSQL store implementations
│   │   ├── __init__.py
│   │   ├── connection.py             # AsyncPG pool, per-tenant schema resolution
│   │   ├── session_store.py          # PostgresSessionStore
│   │   ├── workflow_store.py         # PostgresWorkflowStore
│   │   ├── vault_index.py            # PostgresVaultIndex (pg_trgm + tsvector)
│   │   ├── alarm_store.py            # PostgresAlarmStore
│   │   ├── dream_store.py            # PostgresDreamStore
│   │   ├── user_store.py             # PostgresUserStore
│   │   ├── heartbeat_store.py        # PostgresHeartbeatStore
│   │   ├── broker_store.py           # PostgresBrokerStore
│   │   ├── migrations/
│   │   │   ├── 001_initial.sql
│   │   │   ├── 002_fts_indexes.sql
│   │   │   └── ...
│   │   └── schema_provisioner.py     # Creates schema for new tenant
│   │
│   ├── s3_storage/                   # S3/MinIO file storage
│   │   ├── __init__.py
│   │   ├── client.py                 # S3 client wrapper (MinIO + AWS compatible)
│   │   ├── vault_storage.py          # FileStorageProtocol impl for vault
│   │   ├── upload_storage.py         # Binary upload handling
│   │   ├── presign.py                # Pre-signed URL generation
│   │   └── lifecycle.py              # Temp file cleanup rules
│   │
│   ├── auth/                         # Auth adapters
│   │   ├── __init__.py
│   │   ├── base.py                   # AuthProviderProtocol base implementation
│   │   ├── firebase.py               # Firebase Auth (Lite — same as current)
│   │   ├── oidc.py                   # OpenID Connect
│   │   ├── saml.py                   # SAML 2.0
│   │   ├── ldap.py                   # LDAP/Active Directory
│   │   ├── basic.py                  # Username/password
│   │   └── custom.py                 # Plugin interface for custom auth
│   │
│   ├── tenant/                       # Tenant management
│   │   ├── __init__.py
│   │   ├── manager.py                # Tenant CRUD, provisioning, suspension
│   │   ├── provisioner.py            # Creates PG schema + S3 bucket
│   │   ├── quotas.py                 # Quota enforcement
│   │   └── models.py                 # Tenant, Plan, Quota dataclasses
│   │
│   ├── gateway/                      # LLM proxy (Lite)
│   │   ├── __init__.py
│   │   ├── proxy.py                  # LLM request proxy
│   │   ├── router.py                 # Model routing per tenant plan
│   │   ├── usage.py                  # Token counting, cost tracking
│   │   ├── rate_limiter.py           # Per-tenant rate limits
│   │   └── keys.py                   # Platform API key management
│   │
│   ├── billing/                      # Billing (Lite)
│   │   ├── __init__.py
│   │   ├── stripe.py                 # Stripe integration
│   │   ├── plans.py                  # Plan definitions
│   │   ├── usage_tracker.py          # Usage aggregation
│   │   └── webhooks.py               # Stripe webhook handler
│   │
│   ├── sandbox/                      # Sandbox enforcement
│   │   ├── __init__.py
│   │   ├── middleware.py             # Request-level sandbox
│   │   ├── tool_filter.py            # Tool registration filter
│   │   └── policies.py               # Per-plan sandbox policies
│   │
│   ├── enterprise/                   # Enterprise-specific
│   │   ├── __init__.py
│   │   ├── audit.py                  # Audit logging
│   │   ├── compliance.py             # Data retention, export
│   │   └── monitoring.py             # Health checks, Prometheus metrics
│   │
│   ├── server/                       # Cloud FastAPI app
│   │   ├── __init__.py
│   │   ├── app.py                    # Creates app, overrides stores
│   │   ├── middleware.py             # Tenant resolver, store injector
│   │   ├── config.py                 # Cloud config (YAML/TOML)
│   │   └── routes/
│   │       ├── admin.py              # Admin dashboard API
│   │       ├── tenants.py            # Tenant management API
│   │       └── health.py             # Cloud health/status
│   │
│   └── main.py                       # Entry point
│
├── deploy/
│   ├── docker/
│   │   ├── Dockerfile
│   │   ├── docker-compose.yml        # Development (PG + MinIO + nexus-cloud)
│   │   └── docker-compose.enterprise.yml  # Enterprise on-prem
│   ├── k8s/
│   │   ├── Chart.yaml
│   │   ├── values.yaml
│   │   ├── templates/
│   │   │   ├── deployment.yaml
│   │   │   ├── service.yaml
│   │   │   ├── configmap.yaml
│   │   │   ├── ingress.yaml
│   │   │   ├── postgres-statefulset.yaml
│   │   │   └── minio-statefulset.yaml
│   │   └── enterprise-values-example.yaml
│   └── terraform/                    # Optional infrastructure as code
│
├── admin-ui/                         # Admin dashboard frontend (separate SPA)
│   ├── package.json
│   └── src/
│       ├── App.tsx
│       ├── pages/
│       │   ├── Dashboard.tsx         # Usage overview
│       │   ├── Tenants.tsx           # Tenant management
│       │   ├── Billing.tsx           # Billing & plans
│       │   └── Audit.tsx             # Audit logs (enterprise)
│       └── api/
│           └── admin.ts              # Admin API client
│
├── tests/
│   ├── unit/
│   ├── integration/
│   └── conftest.py
│
└── README.md
```

---

## Feature Matrix

| Feature | Self-Hosted (Free) | Nexus Lite (SaaS) | Nexus Enterprise |
|---|---|---|---|
| **Chat + Agent Loop** | Full | Sandboxed | Sandboxed (configurable) |
| **Vault (markdown files)** | Full filesystem | S3-backed, size-limited | S3/MinIO-backed |
| **Skills (agent-authored)** | Full | User-created (sandboxed), curated set | Full |
| **Knowledge Graph / GraphRAG** | Full | Shared embeddings | Dedicated |
| **Kanban Boards** | Full | Included | Included |
| **Calendar + Alarms** | Full | Included | Included |
| **Workflows** | Full | Limited (no fs_watch, no terminal, no exec) | Full |
| **Local LLM** | Full | **Removed** | **Removed** (optional on-prem) |
| **Terminal Tool** | Full | **Removed** | Optional (admin-controlled) |
| **Tunnel / Sharing** | Full | **Removed** (web-native access) | VPN/VPC-based |
| **Config Editing** | Full | **Removed** (managed) | Admin-only |
| **Self-Update** | Full | **Removed** (managed) | **Removed** |
| **Dream Engine** | Full | **Removed** | Optional |
| **MCP Servers** | Full | **Removed** (security risk) | Admin-curated |
| **DuckDB / Data Tables** | Full | Limited (no exec in transforms) | Full |
| **Voice / TTS** | Full | Optional add-on | Optional |
| **Multi-User** | Self-managed | **Platform-managed** | Company-managed |
| **LLM Providers** | User's own keys | **Platform-provided** (nexus-model.us gateway) | User's own keys |
| **Import Wizard** | Full | Full | Full |
| **Vault History (Git)** | Full | Object versioning | Object versioning |
| **Subagents** | Full | Limited (depth cap, no terminal) | Full |
| **Push Notifications** | Full | Browser push | Browser push |
| **OCR** | Full | Server-side (shared) | Server-side |
| **Web Search / Scrape** | Full | Included (shared quotas) | Included |
| **Max Storage** | Unlimited | Tier-based quota | Contract-based |
| **Concurrent Sessions** | Unlimited | Tier-based limit | Contract-based |
| **Deployment** | User's machine | Our cloud | Their infrastructure |
| **Database** | SQLite | PostgreSQL (shared cluster) | PostgreSQL (their instance) |
| **File Storage** | Filesystem | AWS S3 | MinIO or AWS S3 |
| **Auth** | Loopback/Tunnel | Firebase (same as now) | OIDC/SAML/LDAP/Basic |
| **Data Ownership** | User's machine | Platform (multi-tenant) | Their infrastructure |

---

## Sandboxing Strategy for Lite

### Disabled Tools

```python
LITE_DISABLED_TOOLS = frozenset({
    "terminal",           # Arbitrary code execution
    "state_tool",         # Local filesystem state
    "http_call",          # Unrestricted outbound HTTP (or allowlisted only)
    "acp_call",           # ACP protocol calls (stub, but block anyway)
})
```

### Restricted Tools

- `skill_manage`: Create/edit allowed, delete requires confirmation, no venv creation
- `workflow` transform steps: `template` mode only (no `script` mode with exec, no `llm` mode with uncontrolled prompts)
- `http_call`: Optionally allowlisted domains only, or completely disabled
- `ask_user`: Works normally (HITL is safe)

### Sandbox Policy per Plan

```python
LITE_SANDBOX = SandboxPolicy(
    disabled_tools={"terminal", "state_tool", "http_call", "acp_call"},
    max_file_size=10 * 1024 * 1024,          # 10 MiB
    max_vault_size=1 * 1024 * 1024 * 1024,    # 1 GiB
    max_concurrent_sessions=3,
    daily_turn_limit=100,                      # Plan-dependent
    allowed_http_domains=None,                 # Disabled
    allow_skill_creation=True,
    allow_workflow_exec=True,                  # But restricted steps only
)

ENTERPRISE_SANDBOX = SandboxPolicy(
    disabled_tools=set(),                      # Admin-configurable
    max_file_size=100 * 1024 * 1024,           # 100 MiB
    max_vault_size=50 * 1024 * 1024 * 1024,    # 50 GiB
    max_concurrent_sessions=50,
    daily_turn_limit=0,                        # Unlimited
    allowed_http_domains=None,                 # All
    allow_skill_creation=True,
    allow_workflow_exec=True,                  # Full
)
```

### Enforcement Points

1. **Tool registration** (`registry.py`): `_should_register()` already gates by feature. Extend to also gate by sandbox policy.
2. **Middleware**: `SandboxMiddleware` checks tool calls in-flight, blocks disabled tools.
3. **Agent loop**: Inject `ALLOWED_TOOLS` ContextVar from sandbox policy before each turn.
4. **File operations**: `S3FileStorage.write()` checks `max_file_size` and quota.
5. **Workflow engine**: `engine.py` step dispatch checks sandbox policy for restricted step types.

### New Feature Flags

```python
# Added to features.py ALL_FEATURES
ALL_FEATURES = frozenset({
    # ... existing ...
    "lite",        # Nexus Lite (SaaS multi-tenant)
    "enterprise",  # Nexus Enterprise (on-prem company deployment)
})

# Lite-specific tool restrictions (in addition to existing FEATURE_TOOLS)
LITE_DISABLED_TOOLS = frozenset({
    "terminal", "state_tool", "http_call", "acp_call",
})
```

---

## Database Strategy: PostgreSQL

### Schema-per-Tenant Approach

```sql
-- Global schema (public): tenant registry, billing, platform auth
CREATE TABLE tenants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug TEXT UNIQUE NOT NULL,
    plan TEXT NOT NULL CHECK (plan IN ('lite', 'enterprise')),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'provisioning')),
    settings JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE tenant_quotas (
    tenant_id UUID REFERENCES tenants(id),
    max_storage_bytes BIGINT,
    max_concurrent_sessions INT,
    daily_turn_limit INT,
    monthly_token_limit BIGINT
);

CREATE TABLE platform_users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(id),
    email TEXT NOT NULL,
    display_name TEXT,
    role TEXT NOT NULL DEFAULT 'member',
    status TEXT NOT NULL DEFAULT 'active',
    firebase_uid TEXT,           -- For Lite
    external_id TEXT,            -- For Enterprise (OIDC subject, LDAP DN, etc.)
    created_at TIMESTAMPTZ DEFAULT now(),
    last_login TIMESTAMPTZ,
    UNIQUE(tenant_id, email)
);

-- Per-tenant schema: tenant_<uuid>
-- Created automatically by schema_provisioner.py

-- Example for a tenant schema:
CREATE SCHEMA tenant_abc123;

-- Inside tenant_abc123:
CREATE TABLE tenant_abc123.sessions (
    id TEXT PRIMARY KEY,
    title TEXT,
    context TEXT,
    model_id TEXT,
    project_id TEXT,
    parent_session_id TEXT,
    hidden BOOLEAN DEFAULT false,
    created_at REAL,
    updated_at REAL
);

CREATE TABLE tenant_abc123.messages (
    session_id TEXT REFERENCES tenant_abc123.sessions(id),
    seq INTEGER,
    role TEXT,
    content TEXT,
    reasoning_content TEXT,
    tool_calls JSONB,
    tool_call_id TEXT,
    created_at REAL,
    PRIMARY KEY (session_id, seq)
);

-- FTS using PostgreSQL full-text search
ALTER TABLE tenant_abc123.messages ADD COLUMN tsv tsvector
    GENERATED ALWAYS AS (to_tsvector('english', coalesce(content, ''))) STORED;
CREATE INDEX idx_messages_fts ON tenant_abc123.messages USING gin(tsv);

-- ... remaining tables mirror current SQLite schema
```

### Why Schema-per-Tenant

- **Clean isolation**: Each tenant's data is in its own schema — matches current per-user-directory model
- **Easy debugging**: Can query a single tenant's schema without filters
- **Migration flexibility**: Can migrate individual tenants independently
- **Connection handling**: `SET search_path TO tenant_<uuid>` at connection checkout
- **Safety**: Even if a query misses a filter, it only sees one tenant's data
- **Scalability**: Can move tenants to different PostgreSQL instances if needed

### Connection Management

```python
# postgres/connection.py
class TenantConnectionPool:
    """Manages per-tenant PostgreSQL connection pools."""

    def __init__(self, dsn: str, max_pools: int = 100, pool_size: int = 5):
        self._dsn = dsn
        self._pools: dict[str, asyncpg.Pool] = {}

    async def acquire(self, tenant_id: str) -> asyncpg.Connection:
        pool = await self._get_pool(tenant_id)
        conn = await pool.acquire()
        await conn.execute(f"SET search_path TO tenant_{tenant_id}")
        return conn

    async def _get_pool(self, tenant_id: str) -> asyncpg.Pool:
        if tenant_id not in self._pools:
            self._pools[tenant_id] = await asyncpg.create_pool(self._dsn)
        return self._pools[tenant_id]
```

### Loom Store Adapters

The adapter pattern wraps Loom's SQLite-locked stores. Each adapter implements the same interface as the Loom store but uses PostgreSQL:

```python
# postgres/session_store.py
class PostgresSessionStore:
    """Drop-in replacement for LoomSessionStore using PostgreSQL.
    Implements SessionStoreProtocol from nexus-interfaces."""

    def __init__(self, pool: TenantConnectionPool, tenant_id: str):
        self._pool = pool
        self._tenant_id = tenant_id

    async def get_or_create(self, session_id, context, project_id):
        async with self._pool.acquire(self._tenant_id) as conn:
            row = await conn.fetchrow(
                "SELECT * FROM sessions WHERE id = $1", session_id
            )
            if row is None:
                await conn.execute(
                    "INSERT INTO sessions (id, context, project_id) VALUES ($1, $2, $3)",
                    session_id, context, project_id
                )
                # ...

    async def replace_history(self, session_id, messages):
        async with self._pool.acquire(self._tenant_id) as conn:
            async with conn.transaction():
                await conn.execute("DELETE FROM messages WHERE session_id = $1", session_id)
                for msg in messages:
                    await conn.execute(
                        "INSERT INTO messages (...) VALUES (...)",
                        ...
                    )

    # ... remaining methods mirror LoomSessionStore interface
```

### FTS5 → PostgreSQL Migration

| SQLite FTS5 | PostgreSQL Equivalent |
|---|---|
| `CREATE VIRTUAL TABLE USING fts5(content)` | `tsvector` generated column + GIN index |
| `SELECT * WHERE messages_fts MATCH ?` | `WHERE tsv @@ to_tsquery(?)` |
| BM25 ranking | `ts_rank(tsv, query)` or `ts_rank_cd` |
| `pg_trgm` trigrams | `pg_trgm` extension for fuzzy matching |

### Vector Store → pgvector

Replace Loom's SQLite-based vector store with `pgvector`:

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE tenant_abc123.embeddings (
    id UUID PRIMARY KEY,
    content TEXT,
    embedding vector(1536),
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX ON tenant_abc123.embeddings USING ivfflat (embedding vector_cosine_ops);
```

---

## Object Storage: S3/MinIO

### Bucket Layout

```
Bucket: nexus-<tenant-slug>
├── vault/                    # Markdown files (replaces ~/.nexus/vault/)
│   ├── notes/
│   ├── projects/
│   ├── dreams/
│   ├── uploads/
│   ├── _system/
│   └── .tool-cache/
├── skills/                   # Agent skills (replaces ~/.nexus/skills/)
├── sessions/                 # Session exports (optional)
├── tmp/                      # Temp files with lifecycle rules (1hr TTL)
│   ├── zip-import/
│   └── csv-app/
└── metadata/                 # Tenant metadata
    └── config.json           # Per-tenant config (replaces ~/.nexus/config.toml)
```

### FileStorageProtocol Implementation

```python
# s3_storage/vault_storage.py
class S3FileStorage:
    """FileStorageProtocol implementation using S3-compatible storage."""

    def __init__(self, client, bucket: str, prefix: str = "vault/"):
        self._client = client
        self._bucket = bucket
        self._prefix = prefix

    async def read(self, path: str) -> bytes:
        response = await self._client.get_object(
            Bucket=self._bucket, Key=f"{self._prefix}{path}"
        )
        return await response["Body"].read()

    async def write(self, path: str, data: bytes) -> None:
        # Check max_file_size before write
        if len(data) > self._sandbox.max_file_size:
            raise QuotaExceededError(...)
        # Check total quota
        if await self._exceeds_quota(len(data)):
            raise QuotaExceededError(...)
        await self._client.put_object(
            Bucket=self._bucket, Key=f"{self._prefix}{path}", Body=data
        )

    async def list(self, prefix: str) -> list[FileInfo]:
        paginator = self._client.get_paginator("list_objects_v2")
        # ...

    async def get_presigned_url(self, path: str, expires: int = 3600) -> str:
        """For large files (images, PDFs), generate pre-signed URLs."""
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": f"{self._prefix}{path}"},
            ExpiresIn=expires,
        )
```

### MinIO vs AWS S3

Both use the same S3-compatible API. Configuration:

```yaml
# Development / Enterprise on-prem
storage:
  kind: minio
  endpoint: http://minio:9000
  access_key_env: MINIO_ACCESS_KEY
  secret_key_env: MINIO_SECRET_KEY
  bucket_prefix: nexus-
  secure: false

# Production SaaS
storage:
  kind: s3
  region: us-east-1
  bucket_prefix: nexus-
  # Uses IAM role or explicit credentials
```

### What Moves to S3 vs Stays in PostgreSQL

| Data | PostgreSQL | S3/MinIO |
|---|---|---|
| Sessions + Messages | Yes | No |
| Vault markdown content | No | Yes |
| Vault metadata (tags, links) | Yes | No |
| Vault FTS index | Yes (tsvector) | No |
| Binary uploads | No | Yes |
| Skills (markdown) | No | Yes |
| Config | Yes (JSONB) | No |
| User data | Yes | No |
| Workflow definitions | Yes (in vault, so S3) | Yes |
| Workflow run history | Yes | No |
| Dream state | Yes | No |
| Alarm state | Yes | No |
| TTS voice models | No | Shared bucket (read-only) |
| OCR models | No | Shared bucket (read-only) |

---

## Authentication Architecture

### Nexus Lite: Firebase Auth (Existing)

```
┌─────────────────────────────────────────────┐
│              nexus-model.us                  │
│  Firebase Auth → idToken                     │
│  POST /api/auth/verify → {apiKey, user}      │
│  GET  /api/status → {tier, features[]}       │
└──────────────────┬──────────────────────────┘
                   │
                   v
┌─────────────────────────────────────────────┐
│  Nexus Lite Cloud                            │
│                                              │
│  1. User signs up on nexus-model.us          │
│  2. Selects Lite plan → Stripe subscription  │
│  3. `lite` feature added to user's plan      │
│  4. User accesses lite.nexus-model.us        │
│  5. Firebase popup → idToken                 │
│  6. POST /auth/nexus/verify                  │
│  7. Cloud resolves tenant from account       │
│  8. JWT issued with tenant_id + user role    │
│  9. Subsequent requests carry JWT            │
└─────────────────────────────────────────────┘
```

The flow reuses existing Firebase + nexus-model.us infrastructure. The cloud platform extends the current `AuthManager` with tenant awareness:

```python
# Lite JWT payload
{
    "sub": "user-uuid",
    "tenant_id": "tenant-uuid",
    "role": "member",
    "plan": "lite",
    "exp": ...
}
```

### Nexus Enterprise: Pluggable Auth

```
┌─────────────────────────────────────────────┐
│  Enterprise Identity Provider                │
│  (Azure AD, Okta, LDAP, etc.)               │
└──────────────────┬──────────────────────────┘
                   │
                   v
┌─────────────────────────────────────────────┐
│  Nexus Enterprise Auth Server                │
│                                              │
│  enterprise-config.yaml:                     │
│    auth:                                     │
│      provider: oidc                          │
│      oidc:                                   │
│        issuer: https://login.microsoft...    │
│        client_id: ...                        │
│        roles_claim: roles                    │
│      role_mapping:                           │
│        admin: ["Nexus-Admins"]               │
│        member: ["Nexus-Users"]               │
│                                              │
│  Flow:                                       │
│  1. User visits enterprise deployment        │
│  2. Redirected to identity provider          │
│  3. Auth callback → user info + roles        │
│  4. Role mapping applied                     │
│  5. JWT issued with tenant_id + mapped role  │
│  6. User provisioned locally if first login  │
└─────────────────────────────────────────────┘
```

Enterprise auth configuration:

```yaml
# enterprise-config.yaml
auth:
  provider: oidc                          # oidc | saml | ldap | basic | custom
  oidc:
    issuer: https://login.microsoftonline.com/...
    client_id: ${OIDC_CLIENT_ID}
    client_secret_env: OIDC_CLIENT_SECRET
    scopes: ["openid", "email", "profile"]
    roles_claim: "roles"
    auto_create_users: true
    default_role: member

  role_mapping:
    admin: ["Nexus-Admins", "IT-Department"]
    member: ["Nexus-Users"]
    viewer: ["Nexus-Viewers", "Contractors"]
```

Supported auth providers:

| Provider | Protocol | Use Case |
|---|---|---|
| Firebase | OAuth 2.0 | Lite (reuse existing) |
| Azure AD | OIDC | Enterprise Microsoft shops |
| Google Workspace | OIDC | Enterprise Google shops |
| Okta | OIDC/SAML | Enterprise IdP |
| Active Directory | LDAP | Legacy enterprise |
| SAML 2.0 | SAML | Generic enterprise SSO |
| Basic Auth | HTTP Basic | Simple/internal deployments |
| Custom | Plugin | Any custom identity system |

### Auth Middleware Chain (Cloud)

```python
# server/middleware.py — request flow

# 1. TenantResolver — extracts tenant from JWT/subdomain/host header
# 2. AuthMiddleware — validates JWT, sets request.state.user_id, user_role, tenant_id
# 3. SandboxMiddleware — applies sandbox policy based on plan
# 4. QuotaMiddleware — checks quotas before write operations
# 5. UsageLogger — logs request for billing/audit
# 6. FeatureGateMiddleware — existing feature flag check (unchanged)
# 7. SecurityHeadersMiddleware — existing headers (unchanged)
```

---

## LLM Gateway (Lite)

### Architecture

Platform-provided LLM access means users don't manage API keys. The gateway proxies requests:

```
User Agent → nexus-cloud → LLM Gateway → nexus-model.us/v1 → Provider (OpenAI/Anthropic/etc.)
```

The gateway extends the existing `nexus-model.us` infrastructure:

```python
# gateway/proxy.py
class LLMGateway:
    """Proxies LLM requests from tenant agents to providers."""

    async def proxy_request(self, tenant_id: str, request: LLMRequest) -> LLMResponse:
        # 1. Check rate limits
        await self._rate_limiter.check(tenant_id, request.model)

        # 2. Check daily/monthly token quotas
        await self._usage.check_quota(tenant_id, request.estimated_tokens)

        # 3. Route to appropriate provider
        provider = self._router.resolve(request.model, tenant_plan)

        # 4. Forward request
        response = await provider.complete(request)

        # 5. Track usage
        await self._usage.record(tenant_id, response.usage)

        return response
```

### Rate Limiting

```python
# gateway/rate_limiter.py
class TenantRateLimiter:
    """Per-tenant rate limiting for LLM requests."""

    LIMITS = {
        "free": {"rpm": 5, "tpm": 10000},
        "starter": {"rpm": 20, "tpm": 50000},
        "pro": {"rpm": 60, "tpm": 200000},
        "team": {"rpm": 120, "tpm": 500000},
    }
```

### Usage Tracking

```sql
-- In global schema
CREATE TABLE llm_usage (
    tenant_id UUID REFERENCES tenants(id),
    user_id UUID REFERENCES platform_users(id),
    model TEXT,
    provider TEXT,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    total_tokens INTEGER,
    cost_cents INTEGER,              -- Cost in cents
    request_id TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Aggregation for billing
CREATE MATERIALIZED VIEW daily_usage AS
SELECT
    tenant_id,
    DATE(created_at) as day,
    model,
    SUM(total_tokens) as tokens,
    SUM(cost_cents) as cost_cents,
    COUNT(*) as requests
FROM llm_usage
GROUP BY tenant_id, DATE(created_at), model;
```

---

## Billing & Subscriptions

### Plan Definitions

```python
# billing/plans.py
PLANS = {
    "free": {
        "name": "Free",
        "price_monthly": 0,
        "features": ["chat", "vault", "kanban", "calendar"],
        "quotas": {
            "max_storage_gb": 0.5,
            "max_concurrent_sessions": 1,
            "daily_turn_limit": 20,
            "monthly_token_limit": 100_000,
        }
    },
    "starter": {
        "name": "Starter",
        "price_monthly": 15,
        "features": ["chat", "vault", "kanban", "calendar", "workflow", "database"],
        "quotas": {
            "max_storage_gb": 5,
            "max_concurrent_sessions": 3,
            "daily_turn_limit": 100,
            "monthly_token_limit": 1_000_000,
        }
    },
    "pro": {
        "name": "Pro",
        "price_monthly": 40,
        "features": ["chat", "vault", "kanban", "calendar", "workflow", "database", "knowledge"],
        "quotas": {
            "max_storage_gb": 25,
            "max_concurrent_sessions": 10,
            "daily_turn_limit": 500,
            "monthly_token_limit": 5_000_000,
        }
    },
    "team": {
        "name": "Team",
        "price_monthly": 40,  # per seat
        "features": ["chat", "vault", "kanban", "calendar", "workflow", "database",
                      "knowledge", "multi_user"],
        "quotas": {
            "max_storage_gb": 100,
            "max_concurrent_sessions": 50,
            "daily_turn_limit": 0,  # Unlimited
            "monthly_token_limit": 0,  # Unlimited
        }
    },
}
```

### Stripe Integration

Extends existing nexus-model.us Stripe infrastructure:

- Webhook handler receives `invoice.paid`, `customer.subscription.updated`, etc.
- Plan changes update `tenants.plan` and `tenant_quotas` in PostgreSQL
- Feature flags are derived from plan (same as current system)

---

## Enterprise On-Premise Deployment

### Docker Compose (Development / Small Enterprise)

```yaml
# docker-compose.enterprise.yml
version: "3.8"

services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: nexus_enterprise
      POSTGRES_PASSWORD: ${PG_PASSWORD}
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 5s

  minio:
    image: minio/minio:latest
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: ${MINIO_USER}
      MINIO_ROOT_PASSWORD: ${MINIO_PASSWORD}
    volumes:
      - miniodata:/data
    healthcheck:
      test: ["CMD", "mc", "ready", "local"]
      interval: 5s

  nexus-enterprise:
    build:
      context: .
      dockerfile: deploy/docker/Dockerfile
    ports:
      - "${NEXUS_PORT:-18989}:18989"
    depends_on:
      postgres:
        condition: service_healthy
      minio:
        condition: service_healthy
    volumes:
      - ./enterprise-config.yaml:/app/config.yaml:ro
    environment:
      NEXUS_ENTERPRISE_CONFIG: /app/config.yaml
      PG_DSN: postgresql://postgres:${PG_PASSWORD}@postgres:5432/nexus_enterprise
      S3_ENDPOINT: http://minio:9000
      S3_ACCESS_KEY: ${MINIO_USER}
      S3_SECRET_KEY: ${MINIO_PASSWORD}

volumes:
  pgdata:
  miniodata:
```

### Kubernetes Helm Chart

```yaml
# deploy/k8s/values.yaml
replicaCount: 2

image:
  repository: nexus-cloud
  tag: latest
  pullPolicy: IfNotPresent

service:
  type: ClusterIP
  port: 18989

ingress:
  enabled: true
  className: nginx
  hosts:
    - host: nexus.company.com
      paths:
        - path: /

config:
  auth:
    provider: oidc
    oidc:
      issuer: https://login.microsoftonline.com/...
      client_id: ""
      client_secret_env: OIDC_CLIENT_SECRET
  storage:
    postgres:
      host: postgresql
      port: 5432
      database: nexus_enterprise
    object_storage:
      kind: minio
      endpoint: http://minio:9000

resources:
  requests:
    cpu: "1"
    memory: "2Gi"
  limits:
    cpu: "4"
    memory: "8Gi"

postgresql:
  enabled: true           # Deploy bundled PostgreSQL
  # Or set enabled: false and provide external connection

minio:
  enabled: true           # Deploy bundled MinIO
  # Or set enabled: false and provide external S3
```

### Enterprise Config Schema

```yaml
# enterprise-config.yaml
# Nexus Enterprise Deployment Configuration

# Auth configuration
auth:
  provider: oidc                          # oidc | saml | ldap | basic | custom
  oidc:
    issuer: https://login.microsoftonline.com/...
    client_id: ${OIDC_CLIENT_ID}
    client_secret_env: OIDC_CLIENT_SECRET
    scopes: ["openid", "email", "profile"]
    roles_claim: "roles"
    auto_create_users: true
    default_role: member
  role_mapping:
    admin: ["Nexus-Admins"]
    member: ["Nexus-Users"]
    viewer: ["Nexus-Viewers"]

# Storage configuration
storage:
  postgres:
    host: ${PG_HOST}
    port: 5432
    database: nexus_enterprise
    user: ${PG_USER}
    password_env: PG_PASSWORD
    pool_size: 20
    max_pools: 100
  object_storage:
    kind: minio                           # minio | s3
    endpoint: ${MINIO_ENDPOINT}
    region: ""
    access_key_env: MINIO_ACCESS_KEY
    secret_key_env: MINIO_SECRET_KEY
    bucket_prefix: nexus-ent-
    secure: false

# Agent configuration (enterprise brings their own LLM keys)
agent:
  providers:
    openai:
      base_url: https://api.openai.com/v1
      api_key_env: OPENAI_API_KEY
    anthropic:
      base_url: https://api.anthropic.com
      api_key_env: ANTHROPIC_API_KEY
  default_model: gpt-4o
  max_iterations: 16

# Feature configuration
features:
  - chat
  - kanban
  - calendar
  - workflow
  - knowledge
  - database
  - heartbeat
  - multi_user
  # - dream          # Optional
  # - terminal       # Optional (admin-controlled)

# Enterprise-specific
enterprise:
  audit_logging: true
  data_retention_days: 365
  max_export_size_gb: 10
  monitoring:
    prometheus: true
    health_check_interval: 30
```

### Audit Logging

```python
# enterprise/audit.py
class AuditLogger:
    """Records all user actions for enterprise compliance."""

    async def log(self, tenant_id: str, user_id: str, action: str, details: dict):
        async with self._pool.acquire(tenant_id) as conn:
            await conn.execute(
                """INSERT INTO audit_log (tenant_id, user_id, action, details, created_at)
                   VALUES ($1, $2, $3, $4, now())""",
                tenant_id, user_id, action, json.dumps(details)
            )
```

```sql
CREATE TABLE tenant_<id>.audit_log (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID,
    action TEXT NOT NULL,                -- vault.read, vault.write, session.create, etc.
    details JSONB,
    ip_address INET,
    user_agent TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Auto-partition by month for performance
-- Retention policy: drop partitions older than retention_days
```

---

## Implementation Phases

### Phase 0: nexus-interfaces Package (1-2 weeks)

**Goal**: Define Protocol/ABC interfaces for all swappable components.

**Deliverables**:
- [ ] New repository: `nexus-interfaces`
- [ ] `SessionStoreProtocol` — mirrors LoomSessionStore + Nexus additions
- [ ] `WorkflowStoreProtocol` — mirrors WorkflowStore
- [ ] `VaultIndexProtocol` — mirrors vault_index + vault_search
- [ ] `FileStorageProtocol` — file read/write/list/delete/move
- [ ] `AuthProviderProtocol` — authenticate, validate_session, user CRUD
- [ ] `ConfigProviderProtocol` — load/save config
- [ ] `Tenant` / `SandboxPolicy` dataclasses
- [ ] Published to PyPI or private registry

**No changes to nexus or loom repos.**

### Phase 1: Nexus Core Refactoring (2-3 weeks)

**Goal**: Make Nexus use factory pattern so stores can be swapped without changing existing behavior.

**Deliverables** (in current nexus repo):
- [ ] `store_factory.py` — factory function that creates store instances:
  ```python
  def create_stores(backend: str = "sqlite", **kwargs) -> StoreSet:
      if backend == "sqlite":
          return SqliteStoreSet(**kwargs)
      raise ValueError(f"Unknown backend: {backend}")
  ```
- [ ] Modify `main.py` — use factory instead of direct `SessionStore(db_path)` calls
- [ ] Modify `home.py` — add `set_home_resolver(fn)` hook for cloud to redirect paths
- [ ] Add `"lite"` and `"enterprise"` to `ALL_FEATURES` in `features.py`
- [ ] Add `nexus[cloud]` optional dependency in `pyproject.toml`
- [ ] All existing tests pass (zero behavior change)

**Verification**: `uv run pytest` passes. Self-hosted Nexus works identically.

### Phase 2: nexus-cloud Foundation (4-6 weeks)

**Goal**: PostgreSQL + S3 backends, tenant provisioning, basic multi-tenant server.

**Deliverables** (in new nexus-cloud repo):
- [ ] Project setup: pyproject.toml with `nexus` + `nexus-interfaces` as dependencies
- [ ] PostgreSQL connection management (`TenantConnectionPool`)
- [ ] PostgreSQL store implementations:
  - [ ] `PostgresSessionStore` (largest — sessions, messages, FTS, HITL, feedback)
  - [ ] `PostgresWorkflowStore`
  - [ ] `PostgresVaultIndex` (pg_trgm + tsvector)
  - [ ] `PostgresAlarmStore`
  - [ ] `PostgresDreamStore`
  - [ ] `PostgresUserStore`
  - [ ] `PostgresHeartbeatStore`
- [ ] Schema provisioning (create `tenant_<uuid>` schema for new tenant)
- [ ] Schema migration runner (versioned SQL files, lazy per-tenant)
- [ ] S3/MinIO file storage implementation (`S3FileStorage`)
- [ ] Tenant management (CRUD, provisioning, suspension)
- [ ] Docker Compose for development (PostgreSQL + MinIO + nexus-cloud)
- [ ] Integration tests against PostgreSQL and MinIO
- [ ] Basic cloud server that boots, resolves tenant, serves requests

**Verification**: Docker Compose starts, tenant is provisioned, basic chat works through cloud server.

### Phase 3: Nexus Lite — SaaS Multi-Tenant (6-8 weeks)

**Goal**: Hosted platform with platform-provided LLM, billing, sandboxing.

**Deliverables**:
- [ ] LLM gateway (proxy, rate limiting, usage tracking)
  - [ ] Request proxy to nexus-model.us/v1
  - [ ] Per-tenant rate limiting (RPM, TPM, daily)
  - [ ] Token usage tracking per tenant
  - [ ] Cost tracking for billing
- [ ] Sandbox enforcement
  - [ ] `SandboxMiddleware` — blocks disabled tools
  - [ ] Tool filter in agent registry
  - [ ] File size / quota checks in S3 storage
  - [ ] Workflow step restrictions (no exec, no terminal)
- [ ] Billing integration
  - [ ] Stripe plan definitions
  - [ ] Webhook handler for subscription events
  - [ ] Usage aggregation (daily, monthly)
  - [ ] Quota enforcement middleware
- [ ] Tenant-aware middleware
  - [ ] `TenantResolver` — extracts tenant from JWT
  - [ ] `StoreInjector` — injects per-tenant store instances
  - [ ] `QuotaEnforcer` — checks quotas before operations
  - [ ] `UsageLogger` — logs usage events
- [ ] Subscription flow integration
  - [ ] Extend nexus-model.us to create tenant on Lite subscription
  - [ ] Feature flag `lite` granted based on subscription
  - [ ] Firebase Auth → tenant resolution
- [ ] Admin dashboard API
  - [ ] Tenant list, details, usage metrics
  - [ ] User management within tenant
  - [ ] Billing overview
- [ ] Lite-specific configuration (no local LLM, no terminal, no MCP, etc.)

**Verification**: Full Lite flow works — sign up, provision, chat with platform-provided LLM, quota enforcement, billing webhook.

### Phase 4: Nexus Enterprise — On-Prem (8-10 weeks)

**Goal**: Deployment package for enterprise infrastructure with custom auth.

**Deliverables**:
- [ ] Auth adapters
  - [ ] OIDC adapter (Azure AD, Google Workspace, Okta)
  - [ ] SAML 2.0 adapter
  - [ ] LDAP/Active Directory adapter
  - [ ] Basic auth adapter (username/password)
  - [ ] Custom auth plugin interface
  - [ ] Role mapping configuration
- [ ] Enterprise configuration system (YAML-based)
- [ ] Docker Compose enterprise deployment
- [ ] Kubernetes Helm chart
  - [ ] ConfigMap for enterprise config
  - [ ] Secrets for credentials
  - [ ] Optional bundled PostgreSQL + MinIO
  - [ ] Ingress configuration
- [ ] Audit logging
  - [ ] All user actions logged to per-tenant audit table
  - [ ] Auto-partitioning by month
  - [ ] Retention policy enforcement
- [ ] Compliance features
  - [ ] Data export (GDPR)
  - [ ] Tenant data dump (for migration)
  - [ ] Data retention policies
- [ ] Monitoring
  - [ ] Prometheus metrics endpoint
  - [ ] Health check endpoints
  - [ ] Readiness/liveness probes for Kubernetes
- [ ] Documentation
  - [ ] Deployment guide (Docker Compose)
  - [ ] Deployment guide (Kubernetes)
  - [ ] Auth configuration guide
  - [ ] Enterprise config reference

**Verification**: Enterprise Docker Compose boots, OIDC auth works, full agent functionality with enterprise LLM keys, audit log captures all actions.

---

## Technical Risks & Mitigations

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| **Loom SQLite lock-in** | High | Certain | Build adapter classes that match Loom interfaces. Long-term: contribute Protocols upstream to Loom. |
| **FTS5 → PG full-text search parity** | Medium | Medium | Use `pg_trgm` + `tsvector`. Test search quality with real vault data. BM25 ranking via `ts_rank`. |
| **Vector store → pgvector migration** | Medium | Low | pgvector is well-supported. IVFFlat or HNSW indexes for scale. |
| **Agent sandboxing bypass** | Critical | Medium | Defense in depth: tool registration filter + middleware + agent loop ContextVar. Regular security audits. |
| **S3 latency vs filesystem** | Medium | Medium | Pre-signed URLs for large files. Caching layer for hot vault files. Consider local disk cache for active session files. |
| **Schema migration at scale** | Medium | Medium | Per-tenant isolation allows lazy migrations. Version tracking per-tenant. Can roll back individual tenants. |
| **Single Agent instance → multi-tenant** | High | Medium | Either (a) pool of agents or (b) shared agent with tenant-scoped ContextVars. Start with (b), optimize later. |
| **Connection pool exhaustion** | High | Medium | Pool per tenant with configurable size. Idle pool cleanup. Monitor with Prometheus. |
| **Three-package dependency management** | Low | Medium | Clear versioning strategy. nexus-interfaces is minimal (no breaking changes). CI tests all combos. |
| **Loom version compatibility** | Medium | Medium | Pin loom version in nexus-cloud. Adapters wrap, don't modify — version changes are isolated. |

---

## What This Preserves

- **Current Nexus repo** remains a standalone, single-user, self-hosted downloadable app
- **No behavior changes** for existing users — SQLite is still the default backend
- **Loom dependency** is untouched (adapters wrap, don't modify)
- **Feature flag system** is extended, not replaced — `lite` and `enterprise` are just new features
- **Subscription infrastructure** (nexus-model.us, Firebase, Stripe) is reused as-is
- **Frontend** is shared — same React app, same views, same features. Cloud adds auth screens.
- **Existing multi-user mode** continues to work for self-hosted users who enable it

---

## Open Items for Discussion

1. **Domain naming**: `lite.nexus-model.us` vs a new domain (e.g., `nexus.ai`, `app.nexus.ai`)
2. **Tenant provisioning timing**: Eagerly (on Stripe subscription creation) or lazily (on first user access)?
3. **Admin dashboard UI**: Separate SPA in nexus-cloud repo, or extend the existing Nexus UI with an admin view component?
4. **Mobile app**: Should the Capacitor iOS app support Lite? (Currently targets self-hosted only)
5. **Data migration**: Should we offer data import from self-hosted Nexus → Lite? (Vault import wizard already exists, could extend)
6. **S3 caching**: Do we need a local filesystem cache for hot S3 objects, or is S3 latency acceptable for all vault operations?
7. **Agent instance model**: Shared agent with ContextVars vs agent pool per tenant?
8. **WebSocket vs SSE**: SSE works well for single-user. For multi-tenant at scale, should we consider WebSocket?
9. **Redis**: Do we need Redis for pub/sub, rate limiting, and session state? Or can we use PostgreSQL LISTEN/NOTIFY?
10. **CI/CD**: How to test the multi-tenant setup? Docker Compose in CI with PostgreSQL + MinIO?

---

## Appendix: Store Inventory (SQLite → PostgreSQL Mapping)

| SQLite Store | PostgreSQL Table(s) | Key Differences |
|---|---|---|
| `sessions.sqlite` → `sessions`, `messages` | `tenant_<id>.sessions`, `tenant_<id>.messages` | tsvector for FTS instead of FTS5; JSONB for tool_calls |
| `sessions.sqlite` → `messages_fts` | `tenant_<id>.messages.tsv` (generated column + GIN index) | PostgreSQL full-text search |
| `sessions.sqlite` → `message_feedback` | `tenant_<id>.message_feedback` | Same structure |
| `sessions.sqlite` → `hitl_events`, `hitl_pending` | `tenant_<id>.hitl_events`, `tenant_<id>.hitl_pending` | Same structure |
| `sessions.sqlite` → `projects` | `tenant_<id>.projects` | Same structure |
| `dream_state.sqlite` → `dream_runs`, etc. | `tenant_<id>.dream_runs`, etc. | Same structure |
| `workflow_runs.sqlite` → `workflow_runs`, etc. | `tenant_<id>.workflow_runs`, etc. | Same structure |
| `vault_index.sqlite` → `vault_fts` | `tenant_<id>.vault_fts` (tsvector) | PostgreSQL FTS |
| `vault_meta.sqlite` → `file_tags`, `file_links`, `file_meta` | `tenant_<id>.file_tags`, etc. | Same structure |
| `heartbeat.db` → `heartbeat_fire_log`, `alarm_state` | `tenant_<id>.heartbeat_fire_log`, etc. | Same structure |
| `server.sqlite` → `users`, `invites`, `resource_acl` | `public.platform_users`, `tenant_<id>.invites`, etc. | Split: platform users in global schema, tenant-specific in tenant schema |
| `broker_webhooks.db` → `broker_webhooks` | `tenant_<id>.broker_webhooks` | Same structure |
| `memory.sqlite` → Loom memory tables | `tenant_<id>.memory_*` | Same structure |
| Vector store (SQLite) → embeddings | `tenant_<id>.embeddings` (pgvector) | pgvector instead of SQLite vectors |
| Entity graph (SQLite) → entities, relations | `tenant_<id>.entities`, `tenant_<id>.relations` | Same structure |
| GraphRAG chunks/entities/vectors | `tenant_<id>.graphrag_*` | pgvector for vectors |
