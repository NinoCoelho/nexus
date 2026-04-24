# Memory-Vault Merge Plan (Final)

## Goal

Make `MemoryStore` vault-aware in loom. In nexus, drop `memory_recall` (replaced by `vault_search`), keep `memory_write`/`memory_read` as convenience wrappers. Memory writes automatically get GraphRAG indexing.

## Decisions

1. **Implementation in loom** — `MemoryStore` gets optional `vault_provider` param
2. **Fallback preserved** — no vault = current standalone behavior unchanged
3. **Drop `memory_recall`** — `vault_search` already searches the whole vault
4. **Keep `memory_write`/`memory_read`** — convenience wrappers with key validation, auto-frontmatter, content limits
5. **`memory_write` gains `tags` param** — written as YAML frontmatter
6. **Plain FTS5 ranking** — no salience port (vault_search doesn't have salience)
7. **GraphRAG indexing** — triggered in nexus after each memory write
8. **Startup auto-migration** — move old `~/.nexus/memory/` files to `vault/memory/`

---

## Phase 1 — loom: `FilesystemVaultProvider.search_scoped()`

### File: `loom/store/vault.py`

Add method to both `VaultProvider` protocol and `FilesystemVaultProvider`:

```python
# In VaultProvider protocol:
async def search_scoped(
    self, query: str, path_prefix: str, limit: int = 10
) -> list[dict[str, Any]]: ...

# In FilesystemVaultProvider:
async def search_scoped(
    self, query: str, path_prefix: str, limit: int = 10
) -> list[dict[str, Any]]:
```

Implementation: same FTS5 query as `search()`, add `AND path LIKE ?` with `{path_prefix}%`. Post-filter if needed.

### File: `loom/store/__init__.py`

No changes needed — exports stay the same.

---

## Phase 2 — loom: `MemoryStore` vault-aware

### File: `loom/store/memory.py`

Add params to `__init__`:

```python
def __init__(
    self,
    memory_dir: Path,
    index_db: Path | None = None,
    *,
    embedding_provider: EmbeddingProvider | None = None,
    vault_provider: VaultProvider | None = None,
    vault_prefix: str = "memory",
) -> None:
    ...
    self._vault = vault_provider
    self._vault_prefix = vault_prefix
```

Every public method gets a guard:

```python
async def write(self, key, content, ...):
    if self._vault is not None:
        return await self._write_via_vault(key, content, ...)
    return self._write_standalone(key, content, ...)
```

### Vault-delegated methods

**`_write_via_vault(key, content, category, tags, ...)`**:
- Build metadata dict: `{category, tags, pinned, importance, created, updated, access_count}`
- Call `await self._vault.write(f"{self._vault_prefix}/{key}.md", content, metadata=metadata)`
- Skip local FTS5/meta/index writes — vault handles its own indexing
- Still do embedding write if embedder configured (using vault's read for content)

**`_read_via_vault(key)`**:
- Call `raw = await self._vault.read(f"{self._vault_prefix}/{key}.md")`
- Parse frontmatter to extract metadata
- Return `MemoryEntry`

**`_delete_via_vault(key)`**:
- Call `await self._vault.delete(f"{self._vault_prefix}/{key}.md")`

**`recall()` with vault**:
- Call `await self._vault.search_scoped(query, path_prefix=self._vault_prefix, limit=limit)`
- Convert vault search results to `RecallHit` format (no salience/reranking — plain FTS5)
- Still touch entries (bump access_count in frontmatter)

**`recent()` with vault**:
- Call `await self._vault.list(prefix=self._vault_prefix)`
- Read top N files, sort by mtime, return previews

**`pin()`, `set_importance()`, `touch()` with vault**:
- Read file → update frontmatter dict → write back via vault

### Rename internals

Current private methods (`_write_file`, `_read_file`) become the standalone path, renamed to `_write_standalone`, `_read_standalone` for clarity. Their logic stays identical.

---

## Phase 3 — loom: Tests

### File: `loom/tests/test_memory_store.py`

**New fixtures:**

```python
@pytest.fixture
def vault_dir(tmp_dir):
    d = tmp_dir / "vault"
    d.mkdir()
    return d

@pytest.fixture
def vault_provider(vault_dir):
    return FilesystemVaultProvider(vault_dir)

@pytest.fixture
def store_with_vault(vault_provider, tmp_dir):
    memory_dir = tmp_dir / "memory_standalone"  # minimal, mostly unused
    memory_dir.mkdir()
    ms = MemoryStore(
        memory_dir,
        tmp_dir / "mem_idx.sqlite",
        vault_provider=vault_provider,
    )
    yield ms
    ms.close()
    vault_provider.close()
```

**Strategy**: Parametrize existing tests to run against both `store` (no vault) and `store_with_vault` (with vault). Most tests should pass identically.

**New vault-specific tests:**
- Write via MemoryStore → verify file at `vault_dir/memory/<key>.md` with correct frontmatter
- Read via MemoryStore → reads from vault
- Delete via MemoryStore → removes from vault
- Recall via MemoryStore → uses `search_scoped`, only returns relevant hits
- Pin/touch → frontmatter updated in vault file
- Vault FTS5 index contains the written content

### File: `loom/tests/test_vault.py` (new or extend)

- Test `search_scoped()` filters by path prefix correctly
- Write files at `notes/a.md` and `memory/b.md` with overlapping content
- `search_scoped("test", "memory")` → only `memory/b.md`
- `search_scoped("test", "")` → both files

---

## Phase 4 — nexus: Rewrite memory_tool.py

### File: `agent/src/nexus/tools/memory_tool.py`

**Remove**: `MEMORY_DIR`, `_LOOM_STORE_DIR`, `_MEMORY_INDEX`, `_store`, `_get_store()`, `MEMORY_RECALL_TOOL`, `MemoryHandler.recall()`

**Keep**: `_validate_key()`, `MemoryHandler` class shape

**New implementation:**

```python
"""Memory agent tools: memory_read, memory_write.

Convenience wrappers for the vault memory/ subfolder. Writes go to
vault/memory/<key>.md with YAML frontmatter. Search is via vault_search.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..agent.llm import ToolSpec

_VAULT_ROOT = Path("~/.nexus/vault").expanduser()
_MEMORY_PREFIX = "memory"

_KEY_RE = re.compile(r"^[a-z0-9/_-]+$")
_MAX_KEY_LEN = 128
_MAX_CONTENT_LEN = 50_000


MEMORY_READ_TOOL = ToolSpec(
    name="memory_read",
    description=(
        "Read a memory note. Keys use lowercase letters, numbers, hyphens, "
        "and slashes for namespacing (e.g. 'projects/nexus'). "
        "Memory notes live in vault/memory/ and are fully searchable via vault_search."
    ),
    parameters={
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "Memory key (without .md suffix).",
            },
        },
        "required": ["key"],
    },
)

MEMORY_WRITE_TOOL = ToolSpec(
    name="memory_write",
    description=(
        "Write or overwrite a memory note. Use to persist facts, user "
        "preferences, or project context across sessions. Content is plain "
        "markdown. Overwrites existing content at the key."
    ),
    parameters={
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "Memory key (without .md suffix).",
            },
            "content": {
                "type": "string",
                "description": "Markdown content to store.",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional tags for categorization.",
            },
        },
        "required": ["key", "content"],
    },
)


def _validate_key(key: str) -> str | None:
    """Return an error string if invalid, else None."""
    if not key:
        return "error: key must not be empty"
    if len(key) > _MAX_KEY_LEN:
        return f"error: key too long (max {_MAX_KEY_LEN} chars)"
    if ".." in key:
        return "error: key must not contain '..'"
    if not _KEY_RE.match(key):
        return "error: key must match [a-z0-9/_-]+"
    return None


def _build_frontmatter(tags: list[str] | None) -> str:
    import yaml
    fm: dict = {}
    if tags:
        fm["tags"] = tags
    if not fm:
        return ""
    return f"---\n{yaml.dump(fm, default_flow_style=False).strip()}\n---\n"


class MemoryHandler:
    """Stateless handler — safe to instantiate per call."""

    async def read(self, key: str) -> str:
        err = _validate_key(key)
        if err:
            return err
        from .. import vault
        try:
            result = vault.read_file(f"{_MEMORY_PREFIX}/{key}.md")
            return result["content"]
        except FileNotFoundError:
            return f"error: memory key {key!r} not found"

    async def write(self, key: str, content: str, tags: list[str] | None = None) -> str:
        err = _validate_key(key)
        if err:
            return err
        if len(content) > _MAX_CONTENT_LEN:
            return f"error: content too large (max {_MAX_CONTENT_LEN} chars)"
        from .. import vault
        full = _build_frontmatter(tags) + content
        path = f"{_MEMORY_PREFIX}/{key}.md"
        vault.write_file(path, full)
        # Trigger GraphRAG indexing
        from .vault_tool import _trigger_graphrag_index
        _trigger_graphrag_index(path, full)
        return "ok"
```

### Key points:
- `memory_write` → `vault.write_file()` + `_trigger_graphrag_index()`
- `memory_read` → `vault.read_file()`
- No `memory_recall` at all — agent uses `vault_search` for retrieval
- `vault.write_file()` already handles: atomic write, FTS5 index, tag index, link index, graph cache invalidation

---

## Phase 5 — nexus: Update _loom_bridge.py

### File: `agent/src/nexus/agent/_loom_bridge.py`

**Remove**: `MEMORY_RECALL_TOOL` import and registration, `_mem_recall` closure

**Update**: `_mem_write` closure to pass `tags`:

```python
# memory_read / memory_write (memory_recall REMOVED)
async def _mem_read(args: dict) -> str:
    return await MemoryHandler().read(args.get("key", ""))

async def _mem_write(args: dict) -> str:
    return await MemoryHandler().write(
        args.get("key", ""),
        args.get("content", ""),
        tags=args.get("tags"),
    )

registry.register(_SimpleToolHandler(MEMORY_READ_TOOL, _mem_read))
registry.register(_SimpleToolHandler(MEMORY_WRITE_TOOL, _mem_write))
```

---

## Phase 6 — nexus: Update prompt_builder.py

### File: `agent/src/nexus/agent/prompt_builder.py`

**Update `_memory_summary()`** (lines 15-43):
- Point at `~/.nexus/vault/memory/` instead of `~/.nexus/memory/`

```python
_MEMORY_DIR = Path("~/.nexus/vault/memory").expanduser()
```

**Update IDENTITY prompt** (lines 98-158):

Replace "Two kinds of memory" section with:

```
## Memory & Notes

You have one place to write things down: the **vault** at `~/.nexus/vault/`.

### Quick saves

Use `memory_write` to persist facts, user preferences, or project context.
These land in `vault/memory/` as markdown files with optional tags.
Use `memory_read` to retrieve a specific note by key.

### Searching

Use `vault_search` to search across **all** vault files — including memory
notes, research, project docs, and everything else. When the user references
past events, decisions, or saved context, search the vault before answering.

### Writing

Use `vault_write` for structured documents — research notes, project files,
kanban boards. Use `memory_write` for quick preference/context saves.
```

The vault section (lines 102-136) stays mostly the same but removes the "Two kinds" framing and the `memory_recall` reference.

---

## Phase 7 — nexus: Migration

### Auto-migration on startup

Add in `prompt_builder.py` or as a startup hook:

```python
def _migrate_legacy_memory():
    old_dir = Path("~/.nexus/memory").expanduser()
    new_dir = Path("~/.nexus/vault/memory").expanduser()
    if not old_dir.exists() or new_dir.exists():
        return
    import shutil
    new_dir.mkdir(parents=True, exist_ok=True)
    for f in old_dir.rglob("*.md"):
        if any(p.startswith(".") for p in f.parts):
            continue
        rel = f.relative_to(old_dir)
        dst = new_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(f), str(dst))
    from ..vault_search import rebuild_from_disk
    rebuild_from_disk()
```

Called from `_memory_summary()` or from the serve command. After migration, `~/.nexus/memory/` only contains `.loom/` (ignored, dead code).

---

## Phase 8 — nexus: Tests

### File: `agent/tests/test_memory_tool.py`

- Monkeypatch vault root to temp directory
- Test read/write through vault
- Test tags written as frontmatter
- Test GraphRAG trigger on write (mock `_trigger_graphrag_index`)
- Remove recall tests

### File: `agent/tests/test_memory_recall.py`

- **Delete this file** — `memory_recall` no longer exists
- If any tests are still valuable, move them to a `test_vault_search.py` or similar

---

## Execution Order

```
Phase 1: loom/store/vault.py — search_scoped()                 [no deps]
  ↓
Phase 2: loom/store/memory.py — vault_provider param            [depends on 1]
  ↓
Phase 3: loom tests                                            [depends on 2]
  ↓
Phase 4: nexus/tools/memory_tool.py — rewrite                  [depends on 2]
  ↓
Phase 5: nexus/agent/_loom_bridge.py — remove recall           [depends on 4]
Phase 6: nexus/agent/prompt_builder.py — update prompt         [depends on 4]
Phase 7: nexus — migration                                     [depends on 4]
Phase 8: nexus tests                                           [depends on 4-7]
```

---

## Files Changed

### loom repo
| File | Change |
|------|--------|
| `src/loom/store/vault.py` | Add `search_scoped()` to protocol + impl |
| `src/loom/store/memory.py` | Add `vault_provider`/`vault_prefix` params, delegate when set |
| `tests/test_memory_store.py` | Add vault-backed fixtures + parametrized tests |
| `tests/test_vault.py` (new) | Test `search_scoped()` |

### nexus repo
| File | Change |
|------|--------|
| `agent/src/nexus/tools/memory_tool.py` | Rewrite — remove recall, delegate to vault |
| `agent/src/nexus/agent/_loom_bridge.py` | Remove `memory_recall` registration, pass `tags` |
| `agent/src/nexus/agent/prompt_builder.py` | Update `_memory_summary()` path, rewrite IDENTITY prompt, add migration |
| `agent/tests/test_memory_tool.py` | Rewrite for vault delegation |
| `agent/tests/test_memory_recall.py` | Delete |
| `README.md` | Update diagrams (deferred) |

**No changes to**: `vault.py` (nexus), `vault_tool.py`, `vault_index.py`, `vault_graph.py`, `vault_search.py`, `graphrag_manager.py`, `app.py`

---

## Data Flow After Merge

```
memory_write(key="project/nexus", content="...", tags=["project"])
  → MemoryHandler.write()
    → vault.write_file("memory/project/nexus.md", frontmatter+content)
      → atomic write to disk
      → vault_search.index_path() — FTS5 index
      → vault_index.reindex_file() — tags, links
      → vault_graph.invalidate_cache()
    → _trigger_graphrag_index("memory/project/nexus.md", content)
      → graphrag_manager.index_vault_file()
        → GraphRAGEngine.index_source() — chunks, embeds, entities

vault_search(query="project nexus API decisions")
  → vault_search.search()
    → FTS5 across ALL vault files (includes memory/)
  → Returns ranked hits from memory/, notes/, research/, etc.
```
