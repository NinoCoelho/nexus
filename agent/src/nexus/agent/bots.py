"""Bot mention resolution — @handle routing for multi-bot conversations.

Any chat can become a group: @mentioning a bot's handle in a user message
routes that turn to the mentioned bot — its persona drives the reply, which
is attributed to it in the transcript. Routing happens at LLM-call time (the
prompt builder resolves the trailing user message), so every surface (web,
Telegram, queued injections, resumed turns) gets it without plumbing.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..server.project_store import Project

# Handles are short slugs; the leading @ is the mention sigil.
MENTION_RE = re.compile(r"(?:^|[\s(>])@([a-z0-9][a-z0-9-]{0,31})", re.IGNORECASE)

# Synthetic user-message prefix for team round passes. The char before the
# @ satisfies MENTION_RE's boundary, so `MARKER@handle` routes like a real
# mention; the UI renders it as a subtle "passing to @handle" chip instead
# of a user bubble.
TEAM_MARKER = "<!-- nx:team -->"


def parse_mentions(text: str) -> list[str]:
    """Return mention handles in order of appearance (duplicates kept)."""
    if not text:
        return []
    return [m.group(1).lower() for m in MENTION_RE.finditer(text)]


def coordinator_identity() -> dict | None:
    """The coordinator's mention identity — ``{id, handle, name}`` or None.

    The handle derives from the **configured name** (``[coordinator].name``),
    never the literal "main": the user names their bots and @mentions follow
    that name. "main" remains only as the internal/API id. Falls back to
    handle "main" when no name is configured.
    """
    try:
        from ..config_file import load_cached
        from ..server.project_store import bot_handle_from_name

        coord = getattr(load_cached(), "coordinator", None)
        if coord is None or not coord.enabled:
            return None
        name = (coord.name or "").strip() or "Main"
        return {"id": "main", "handle": bot_handle_from_name(name), "name": name}
    except Exception:
        return None


def resolve_mention_target(
    message: str,
    *,
    own_project_id: str | None,
    project_store: Any = None,
    coordinator: dict | None | False = None,
) -> tuple[str, Any] | None:
    """First @mention that reroutes a turn, as ``(kind, target)``.

    Returns ``("bot", Project)`` when a project-backed bot is mentioned,
    ``("coordinator", identity_dict)`` when the *coordinator's* handle is
    mentioned (name-derived, e.g. ``@nico``), or ``None`` when the message
    mentions nothing or only the chat's own bot. Project handles win over a
    colliding coordinator handle. ``coordinator=False`` suppresses
    coordinator matching; ``None`` (default) reads it from config. Callers
    are responsible for suppressing the coordinator result in the master
    chat itself (a self-mention there is a no-op).
    """
    handles = parse_mentions(message)
    if not handles:
        return None
    if coordinator is None:
        coordinator = coordinator_identity()
    if project_store is None:
        from ..home import sessions_db
        from ..server.project_store import ProjectStore

        project_store = ProjectStore(sessions_db())
    for handle in handles:
        try:
            proj = project_store.get_by_handle(handle)
        except Exception:
            proj = None
        if proj is not None:
            if proj.id != own_project_id:
                return ("bot", proj)
            continue  # self-mention of the chat's own bot — not a reroute
        if coordinator is not False and coordinator is not None and handle == coordinator["handle"]:
            return ("coordinator", coordinator)
    return None


def resolve_mention_project(
    message: str,
    *,
    own_project_id: str | None,
    project_store: Any = None,
) -> "Project | None":
    """Back-compat wrapper: the mentioned *project* bot, coordinator aside."""
    target = resolve_mention_target(
        message, own_project_id=own_project_id, project_store=project_store, coordinator=False
    )
    return target[1] if target is not None and target[0] == "bot" else None


def team_followup_messages(
    team: dict | None,
    message: str,
    *,
    project_store: Any = None,
) -> list[str]:
    """Synthetic mention passes for a group-chat (team) message.

    In ``round`` mode every member answers a message that addresses nobody:
    the lead answers normally (the session belongs to it), then each other
    member gets a synthetic ``TEAM_MARKER@handle`` user message — queued on
    the turn runner, so the existing queue-then-inject machinery delivers it
    as a mid-turn injection or a chained turn, and the mention routing in
    the prompt builder does the actual persona switch. Returns ``[]`` when
    the user already @mentioned a member (a directed message goes to that
    member only) or the team isn't in round mode.
    """
    if not team or team.get("mode") != "round":
        return []
    members = [str(m) for m in (team.get("members") or [])]
    if len(members) < 2:
        return []
    if project_store is None:
        from ..home import sessions_db
        from ..server.project_store import ProjectStore

        project_store = ProjectStore(sessions_db())
    mentioned = set(parse_mentions(message))
    if mentioned:
        # A directed message goes to the mentioned member only — no fan-out.
        return []
    out: list[str] = []
    for bot_id in members[1:]:
        try:
            proj = project_store.get(bot_id)
        except Exception:
            proj = None
        if proj is None or not proj.handle:
            continue
        out.append(f"{TEAM_MARKER}@{proj.handle}")
    return out


def attribute_bot_replies(
    messages: list[dict],
    *,
    own_project_id: str | None,
    project_store: Any = None,
    is_master: bool = False,
) -> list[dict]:
    """Annotate a message list with which bot each assistant reply came from.

    Read-time derivation from the transcript: a user message that routes to
    bot X (via ``resolve_mention_project``) attributes every following
    assistant message until the next user message. Assistant replies without
    a routing mention belong to the chat's own bot and get ``bot: null``.
    Mutates nothing; sets ``msg["bot"] = {"id", "handle", "name"} | None``.
    """
    if project_store is None:
        from ..home import sessions_db
        from ..server.project_store import ProjectStore

        project_store = ProjectStore(sessions_db())
    store = project_store
    coord = coordinator_identity()
    cache: dict[str, dict | None] = {}
    current: dict | None = None

    def _role(v: Any) -> str:
        return str(getattr(v, "value", v) or "").lower()

    for msg in messages:
        role = _role(msg.get("role"))
        if role == "user":
            text = msg.get("content")
            if not isinstance(text, str):
                text = ""
            target = resolve_mention_target(
                text, own_project_id=own_project_id, project_store=store,
                coordinator=coord,
            )
            if target is not None:
                kind, val = target
                if kind == "coordinator":
                    # In the master chat the coordinator is the chat's own
                    # identity — mentioning it there is a self-mention.
                    current = None if is_master else dict(val)
                else:
                    current = cache.get(val.id) or {
                        "id": val.id,
                        "handle": val.handle,
                        "name": val.name,
                    }
                    cache[val.id] = current
            else:
                current = None
        elif role == "assistant":
            msg["bot"] = current
    return messages
