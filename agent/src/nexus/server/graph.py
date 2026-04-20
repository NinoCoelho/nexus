"""Build an agent/skill/session graph for the UI graph view."""
from __future__ import annotations

import json
import time

from ..skills.registry import SkillRegistry
from .session_store import SessionStore

# Simple 60-second module-level cache: (expiry_timestamp, data)
_cache: tuple[float, dict] | None = None
_CACHE_TTL = 60.0


def build_agent_graph(
    registry: SkillRegistry,
    store: SessionStore,
    *,
    session_limit: int = 20,
) -> dict:
    global _cache
    now = time.monotonic()
    if _cache is not None and now < _cache[0]:
        return _cache[1]

    result = _build(registry, store, session_limit=session_limit)
    _cache = (now + _CACHE_TTL, result)
    return result


def _build(
    registry: SkillRegistry,
    store: SessionStore,
    *,
    session_limit: int,
) -> dict:
    nodes: list[dict] = []
    edges: list[dict] = []

    # Hub node
    nodes.append({
        "id": "agent:nexus",
        "label": "Nexus",
        "type": "agent",
        "meta": {},
    })

    # Skill nodes
    skills = registry.list()
    skill_names = {s.name for s in skills}
    for skill in skills:
        sid = f"skill:{skill.name}"
        nodes.append({
            "id": sid,
            "label": skill.name,
            "type": "skill",
            "meta": {
                "trust": skill.trust,
                "description": skill.description,
            },
        })
        edges.append({
            "source": sid,
            "target": "agent:nexus",
            "label": "exposes",
        })

    # Session nodes (recent N)
    summaries = store.list(limit=session_limit)
    for sess in summaries:
        nid = f"session:{sess.id}"
        nodes.append({
            "id": nid,
            "label": sess.title or sess.id[:8],
            "type": "session",
            "meta": {
                "message_count": sess.message_count,
                "updated_at": sess.updated_at,
            },
        })
        touched = _skills_touched(store, sess.id, skill_names)
        for sname in touched:
            edges.append({
                "source": nid,
                "target": f"skill:{sname}",
                "label": "used",
            })

    return {"nodes": nodes, "edges": edges}


def _skills_touched(
    store: SessionStore, session_id: str, skill_names: set[str]
) -> set[str]:
    """Return skill names whose tool was called within this session."""
    found: set[str] = set()
    try:
        session = store.get(session_id)
        if session is None:
            return found
        for msg in session.history:
            for tc in msg.tool_calls or []:
                # tool_call names follow the pattern "<skill_name>__<tool>"
                # or just match directly against skill names
                name = tc.name if hasattr(tc, "name") else tc.get("name", "")
                # Check if the tool name starts with a known skill name
                for sname in skill_names:
                    if name == sname or name.startswith(sname + "__") or name.startswith(sname + "_"):
                        found.add(sname)
    except Exception:
        pass
    return found
