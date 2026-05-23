# Fix: Session sidebar bugs (new session not appearing, deleted sessions reappearing)

## Root Cause

Browser caching of `GET /sessions` responses. The frontend `getSessions` fetch doesn't set `cache: "no-store"`, and the backend doesn't return `Cache-Control` headers. Browsers (especially Safari) may serve stale cached responses, causing:

1. **New session missing from sidebar** — after `done`, the re-fetch returns cached (old) list without the new session. The optimistic placeholder is then cleared, and the session vanishes.
2. **Deleted sessions reappear on refresh** — the cached response still contains deleted sessions.
3. **Delete doesn't bump `sessionsRevision`** — so stale local state persists until the next unrelated revision bump.

## Fixes

### Fix 1: `cache: "no-store"` on session list fetches
**File:** `ui/src/api/sessions.ts:138-141`

```ts
// Before
const res = await fetch(`${BASE}/sessions?limit=${limit}`);

// After
const res = await fetch(`${BASE}/sessions?limit=${limit}`, { cache: "no-store" });
```

### Fix 2: Bump `sessionsRevision` after successful delete
**File:** `ui/src/components/Sidebar/useSessionActions.ts:70-77`

```ts
// Before
const handleDelete = async (id: string) => {
  try {
    await deleteSession(id);
    setSessions((prev) => prev.filter((s) => s.id !== id));
    if (id === activeSessionId) onActiveSessionDeleted();
  } catch { /* ignore */ }
  setMenuNull();
};

// After
const handleDelete = async (id: string) => {
  try {
    await deleteSession(id);
    setSessions((prev) => prev.filter((s) => s.id !== id));
    if (id === activeSessionId) onActiveSessionDeleted();
    onSessionsRevisionBump();
  } catch { /* ignore */ }
  setMenuNull();
};
```

### Fix 3: `Cache-Control: no-cache` on backend `GET /sessions`
**File:** `agent/src/nexus/server/routes/sessions.py:110-126`

Add a `Response` parameter to set the header:
```python
from fastapi import Response

@router.get("/sessions")
async def list_sessions(
    limit: int = 50,
    include_hidden: bool = False,
    store: SessionStore = Depends(get_sessions),
    response: Response = None,
):
    response.headers["Cache-Control"] = "no-cache"
    summaries = store.list(limit=limit, include_hidden=include_hidden)
    return [...]
```

## Out of scope (deferred)

- **SSE reconnection for in-progress sessions after refresh** — complex change, separate task.
