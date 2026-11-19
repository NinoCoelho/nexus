"""Agent bootstrap — one URL that configures any external coding agent.

``GET /bootstrap`` serves a living markdown document: what Nexus is, how
to verify the CLI, a summary of delegation, and **links to skills** the
agent fetches on demand (progressive disclosure — the entry document
stays small). ``GET /bootstrap/skills/{name}.md`` serves each skill.

The user's whole handoff becomes one sentence: *"curl
http://localhost:18989/bootstrap and follow it"* — the agent reads the
doc, picks the skills it needs, self-installs them in its own format,
and verifies with ``nexus bots list``.

Loopback-or-token auth applies as everywhere else: curl from the same
machine needs no token; remote agents go through the tunnel/pairing
token like any other client.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

from ...ext_skill import SKILL_NAME, _all_skills

log = logging.getLogger(__name__)

router = APIRouter()

_SKILLS = _all_skills()


_SKILL_DESCRIPTIONS = {
    SKILL_NAME: (
        "delegate tasks to Nexus bots via `nexus run`. Install when the "
        'user wants you to hand work to bots ("ask @swe to…", "run this '
        'on Nexus")'
    ),
    "nexus-vault": (
        "read/search/write notes in the Nexus vault via `nexus vault`. "
        "Install for knowledge storage and retrieval work"
    ),
    "nexus-kanban": (
        "manage task boards via `nexus kanban` (add/move/update cards). "
        "Install for task tracking work"
    ),
    "nexus-tables": (
        "structured tables + DuckDB SQL via `nexus table` (create tables, "
        "query, import CSV). Install for structured-data and analysis work"
    ),
}


def _base_url(request: Request) -> str:
    """Absolute base for links inside the doc (scheme omitted for http)."""
    port = request.url.port or 18989
    return f"http://127.0.0.1:{port}"


def _server_version() -> str:
    try:
        from ... import __version__

        return __version__
    except Exception:
        return "dev"


def _server_port() -> int:
    try:
        from ..config import PORT

        return int(PORT)
    except Exception:
        return 18989


def _bot_roster() -> list[dict[str, str]]:
    try:
        from ...home import sessions_db
        from ..project_store import ProjectStore

        store = ProjectStore(sessions_db())
        return [
            {
                "handle": s.handle,
                "name": s.name,
                "description": (s.description or "")[:120],
            }
            for s in store.list(limit=50)
        ]
    except Exception:
        log.debug("bootstrap: roster unavailable", exc_info=True)
        return []


def _render_bootstrap(request: Request) -> str:
    base = _base_url(request)
    version = _server_version()
    port = _server_port()
    roster = _bot_roster()
    roster_lines = "\n".join(
        f"- `@{b['handle']}` — **{b['name']}**{' — ' + b['description'] if b['description'] else ''}"
        for b in roster
    ) or "- (no bots configured yet — create them in the Nexus UI)"
    roster_handles = ", ".join(f"@{b['handle']}" for b in roster) or "(none yet)"
    skills_block = _skills_block(base)

    return f"""# Nexus agent bootstrap

You are reading the setup document of a **Nexus** instance — a local,
single-user platform of AI bots. Every bot is a real agent with its own
persona, memory (persistent chat history), skills, and model. You
orchestrate; Nexus bots execute.

**Your task**: if the user asked you to connect to Nexus, install the
skill(s) below into your own configuration, verify with
`nexus bots list --json`, and confirm what you did. If you just needed
information, read on.

## Server context

- Version: `{version}` · Port: `{port}` (CLI overrides: `NEXUS_PORT`)
- Bots available: {roster_handles}

<roster>
{roster_lines}
</roster>

## Verify the CLI (do this first)

```sh
nexus bots list --json
```

If this fails, the CLI isn't installed or the daemon isn't running.
Tell the user: *"install the Nexus CLI (repo NinoCoelho/nexus) and run
`nexus daemon start`"* — do not improvise alternatives.

## Skills

Fetch and install only what your task needs (each document is
self-contained, with self-install steps for Claude Code / OpenCode /
Codex):

{skills_block}

## The 30-second version

```sh
nexus bots list --json                          # discover bots
nexus run @handle "task text" --json            # delegate + final reply
```

Exit codes: `0` ok · `1` turn failed · `2` bot not found.
"""


def _skills_block(base: str) -> str:
    lines = []
    for name in _SKILLS:
        desc = _SKILL_DESCRIPTIONS.get(name, "Nexus skill")
        lines.append(f"- [`{name}`]({base}/bootstrap/skills/{name}.md) — {desc}.")
    return "\n".join(lines)


@router.get("/bootstrap", response_class=PlainTextResponse)
async def bootstrap_doc(request: Request) -> PlainTextResponse:
    return PlainTextResponse(
        _render_bootstrap(request), media_type="text/markdown; charset=utf-8"
    )


@router.get("/bootstrap/skills/{name}.md", response_class=PlainTextResponse)
async def bootstrap_skill(name: str) -> PlainTextResponse:
    body = _SKILLS.get(name)
    if body is None:
        return PlainTextResponse(
            f"# Unknown skill: {name}\n\nAvailable: {', '.join(_SKILLS) or '(none)'}\n",
            status_code=404,
            media_type="text/markdown; charset=utf-8",
        )
    header = (
        f"# Nexus skill: {name}\n\n"
        "Install this into your own configuration (steps at the end of the "
        "document), then verify with the verification command inside the "
        "document.\n\n---\n\n"
    )
    return PlainTextResponse(
        header + body, media_type="text/markdown; charset=utf-8"
    )
