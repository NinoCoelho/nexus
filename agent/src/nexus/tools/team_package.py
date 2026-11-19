"""team_package agent tool — export/import portable bot teams.

Exports a team (bots + personas + skills + rooms + paused routines) into a
single vault-native markdown file with ``team-plugin: 2`` frontmatter, and
imports such a file back into Nexus. Modeled on OpenMausBot's
BotMRR Markdown v2 template folder, collapsed into one portable .md file.

Never carries credentials, chats, memory, or computer access. Routines
always arrive paused (``enabledAfterInstall: false``); the person must
explicitly enable them.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Callable
from typing import Any

import yaml

from .. import vault as _vault
from ..agent.llm import ToolSpec
from ..home import sessions_db
from ..server.project_store import ProjectStore

log = logging.getLogger(__name__)

TEAM_PLUGIN_KEY = "team-plugin"
TEAM_PLUGIN_VERSION = 2


TEAM_PACKAGE_TOOL = ToolSpec(
    name="team_package",
    description=(
        "Export or import a portable bot team as a single vault markdown "
        "file (``team-plugin: 2`` frontmatter). Actions: "
        "'export' (session_id -> a .md file with bots, personas, skills, "
        "rooms, paused routines), 'import' (path -> create bots, teams, "
        "workflows; routines always paused). A packaged team never carries "
        "credentials, chats, memory, or computer access. Import creates "
        "everything fresh with new ids/handles; the person reviews before "
        "confirming (needs confirmed=true for create actions)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["export", "import"],
                "description": "Export a team to a .md file, or import one.",
            },
            "session_id": {
                "type": "string",
                "description": (
                    "Team chat session id to export (for action='export'). "
                    "Its ``team`` column drives the roster."
                ),
            },
            "path": {
                "type": "string",
                "description": (
                    "Vault path of the .md package (for action='import'), "
                    "e.g. teams/my-team.md. For export, the output path "
                    "(default: teams/<slug>.md)."
                ),
            },
            "name": {
                "type": "string",
                "description": "Override team name on import.",
            },
            "confirmed": {
                "type": "boolean",
                "description": (
                    "True only after the user reviewed the import preview "
                    "and approved creation (create/update/delete need it)."
                ),
            },
        },
        "required": ["action"],
    },
)


# ── helpers ────────────────────────────────────────────────────────────

def _bot_store() -> ProjectStore:
    return ProjectStore(sessions_db())


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return s or "team"


def _read_skill(skill_name: str) -> dict[str, str] | None:
    """Read a skill file skills/<name>/SKILL.md from the vault."""
    path = f"skills/{skill_name}/SKILL.md"
    try:
        content = _vault.read_file(path)
    except Exception:
        return None
    raw = content.get("content", "") if isinstance(content, dict) else str(content)
    if not raw.startswith("---"):
        return {"name": skill_name, "description": "", "body": raw.strip()}
    end = raw.find("\n---", 3)
    if end == -1:
        return None
    try:
        fm = yaml.safe_load(raw[3:end]) or {}
    except yaml.YAMLError:
        return None
    body = raw[end + 4:].lstrip("\n")
    return {
        "name": str(fm.get("name", skill_name)),
        "description": str(fm.get("description", "")),
        "body": body.strip(),
    }


def _list_skills() -> list[str]:
    """Return skill folder names that have a SKILL.md."""
    try:
        entries = _vault.list_tree()
    except Exception:
        return []
    skills: set[str] = set()
    for e in entries:
        if e.type != "file":
            continue
        m = re.match(r"^skills/([^/]+)/SKILL\.md$", e.path)
        if m:
            skills.add(m.group(1))
    return sorted(skills)


# ── export ─────────────────────────────────────────────────────────────


async def _export_team(
    session_id: str,
    output_path: str | None,
    store: Any,
    pstore: ProjectStore,
) -> dict[str, Any]:
    from ..server.session_store import SessionStore

    if not isinstance(store, SessionStore):
        # store passed in may be the coordinator's store; ensure SessionStore
        store = SessionStore(sessions_db())

    session = store.get(session_id)
    if session is None:
        return {"ok": False, "error": f"session not found: {session_id}"}

    raw_team = store.get_team(session_id)
    if not raw_team:
        return {"ok": False, "error": "session is not a team chat"}

    try:
        team = json.loads(raw_team) if isinstance(raw_team, str) else raw_team
    except (TypeError, json.JSONDecodeError):
        return {"ok": False, "error": "invalid team data on session"}

    name = str(team.get("name") or session.title or "Untitled Team")
    members = list(team.get("members") or [])
    mode = str(team.get("mode") or "lead")

    if not members:
        return {"ok": False, "error": "team has no members"}

    # Resolve bots.
    bots_out: list[dict[str, Any]] = []
    skill_names: set[str] = set()
    for m in members:
        proj = pstore.get(m)
        if proj is None:
            proj = pstore.get_by_handle(str(m))
        if proj is None:
            continue
        bots_out.append(
            {
                "id": str(uuid.uuid4().hex[:8]),  # new id on import
                "name": proj.name,
                "handle": proj.handle,
                "persona": proj.persona or "",
                "instructions": proj.instructions or "",
                "description": proj.description or "",
                "skills": [],
                "avatar": None,
            }
        )

    if not bots_out:
        return {"ok": False, "error": "no resolvable bots on team"}

    # Skills bodies.
    skills_out: list[dict[str, str]] = []
    for sn in sorted(skill_names):
        sk = _read_skill(sn)
        if sk:
            skills_out.append(sk)

    slug = _slugify(output_path or f"teams/{name}.md")
    if not slug.endswith(".md"):
        slug += ".md"
    if "/" not in slug:
        slug = f"teams/{slug}"

    fm: dict[str, Any] = {
        TEAM_PLUGIN_KEY: TEAM_PLUGIN_VERSION,
        "id": _slugify(name),
        "name": name,
        "tagline": "",
        "summary": team.get("description", "") or "",
        "category": "team",
        "author": "nexus",
        "license": "Apache-2.0",
        "outcomes": [],
        "setupMinutes": 0,
        "requirements": [],
        "team": bots_out,
        "rooms": [
            {
                "id": _slugify(name),
                "name": name,
                "members": [b["handle"] for b in bots_out],
                "mode": mode,
            }
        ],
        "routines": [],  # routines come from workflow triggers on import
        "skills": [
            {"name": s["name"], "description": s["description"]} for s in skills_out
        ],
        "avatars": [],
    }

    body_lines = [f"# {name}", ""]
    if fm["summary"]:
        body_lines += [fm["summary"], ""]
    body_lines += ["## Activation", "", "Install this team with the Nexus import flow.", ""]
    body_lines += ["## Mission", "", "Imported team — see the README in the package.", ""]
    body_lines += ["## Outcomes", "", "- " + "\n- ".join(fm["outcomes"]) if fm["outcomes"] else "- (none declared)", ""]
    body_lines += ["## Connections", "", "None — connect apps after import.", ""]
    body_lines += ["## Team", "", f"{len(bots_out)} bots. Mode: {mode}.", ""]
    body_lines += ["## Chief of Staff", "", "The lead bot (first member) answers unmentioned messages.", ""]
    body_lines += ["## Completion rule", "", "Team installed and bots active.", ""]

    md = _serialize_team(fm, body_lines, skills_out)

    if output_path:
        slug = output_path
    _vault.write_file(slug, md)

    return {
        "ok": True,
        "action": "export",
        "path": slug,
        "team": {
            "name": name,
            "bots": len(bots_out),
            "skills": len(skills_out),
            "mode": mode,
        },
    }


def _serialize_team(
    fm: dict[str, Any],
    body_lines: list[str],
    skills_out: list[dict[str, str]],
) -> str:
    front: dict[str, Any] = {TEAM_PLUGIN_KEY: TEAM_PLUGIN_VERSION}
    for k in (
        "id", "name", "tagline", "summary", "category", "author", "license",
        "outcomes", "setupMinutes", "requirements", "team", "rooms",
        "routines", "skills", "avatars",
    ):
        if k in fm and fm[k] not in (None, [], ""):
            front[k] = fm[k]

    # Inline skill bodies under skills/<name>/SKILL.md are not in one file;
    # we embed skill bodies as a skills section in the body instead.
    skill_body_lines: list[str] = []
    for s in skills_out:
        skill_body_lines.append(f"### {s['name']}")
        if s.get("description"):
            skill_body_lines.append(s["description"])
        skill_body_lines.append("")
        skill_body_lines.append(s.get("body", ""))
        skill_body_lines.append("")

    fm_text = yaml.dump(front, default_flow_style=False, sort_keys=False, allow_unicode=True).rstrip()
    parts = [f"---\n{fm_text}\n---"]
    body_str = "\n".join(body_lines)
    if skill_body_lines:
        body_str += "\n\n## Skills\n\n" + "\n".join(skill_body_lines)
    if body_str.strip():
        parts.append(body_str.rstrip())
    return "\n".join(parts).rstrip() + "\n"


# ── import ─────────────────────────────────────────────────────────────


async def _import_team(
    path: str,
    name_override: str | None,
    confirmed: bool,
    pstore: ProjectStore,
) -> dict[str, Any]:
    try:
        content = _vault.read_file(path)
    except FileNotFoundError:
        return {"ok": False, "error": f"package not found: {path}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"read failed: {exc}"}
    raw = content.get("content", "") if isinstance(content, dict) else str(content)

    if not raw.startswith("---"):
        return {"ok": False, "error": "not a team package (no frontmatter)"}
    end = raw.find("\n---", 3)
    if end == -1:
        return {"ok": False, "error": "malformed frontmatter"}
    try:
        fm = yaml.safe_load(raw[3:end]) or {}
    except yaml.YAMLError as exc:
        return {"ok": False, "error": f"frontmatter parse error: {exc}"}

    if not isinstance(fm, dict) or fm.get(TEAM_PLUGIN_KEY) != TEAM_PLUGIN_VERSION:
        return {"ok": False, "error": f"not a {TEAM_PLUGIN_KEY} v{TEAM_PLUGIN_VERSION} package"}

    team_name = str(name_override or fm.get("name") or "Untitled Team")
    bots_in = list(fm.get("team") or [])
    rooms_in = list(fm.get("rooms") or [])
    routines_in = list(fm.get("routines") or [])
    skills_in = list(fm.get("skills") or [])

    if not bots_in:
        return {"ok": False, "error": "package has no bots"}

    # Preview (dry-run) when not confirmed.
    if not confirmed:
        return {
            "ok": True,
            "preview": True,
            "needs_confirmation": True,
            "team": {
                "name": team_name,
                "bots": len(bots_in),
                "skills": len(skills_in),
                "rooms": len(rooms_in),
                "routines": len(routines_in),
                "routines_will_be_paused": True,
            },
            "bots_preview": [
                {
                    "name": b.get("name"),
                    "handle": b.get("handle"),
                    "persona_preview": (b.get("persona") or "")[:120],
                    "skills": b.get("skills", []),
                }
                for b in bots_in
            ],
            "error": None,
        }

    # Create bots.
    created_ids: list[str] = []
    handle_map: dict[str, str] = {}  # old handle -> new handle
    id_map: dict[str, str] = {}  # old id -> new id
    for b in bots_in:
        b_name = str(b.get("name") or "Untitled Bot")
        b_handle = str(b.get("handle") or _slugify(b_name))
        fields: dict[str, str] = {}
        if b.get("persona"):
            fields["persona"] = str(b["persona"])
        if b.get("instructions"):
            fields["instructions"] = str(b["instructions"])
        if b.get("description"):
            fields["description"] = str(b["description"])

        proj = pstore.create(name=b_name, **fields)
        # Rename handle if desired / ensure unique
        if b_handle != proj.handle:
            try:
                pstore.update(proj.id, handle=b_handle)
            except Exception:
                pass  # keep generated handle
        proj = pstore.get(proj.id)
        created_ids.append(proj.id)
        id_map[b.get("id", "")] = proj.id
        handle_map[str(b.get("handle") or "")] = proj.handle

    # Create skills in vault.
    created_skills: list[str] = []
    for s in skills_in:
        s_name = str(s.get("name") or "Untitled Skill")
        s_desc = str(s.get("description") or "")
        s_body = str(s.get("body") or "")
        slug = _slugify(s_name)
        skill_path = f"skills/{slug}/SKILL.md"
        skill_md = (
            f"---\nname: {s_name}\ndescription: {s_desc}\n---\n\n{s_body}\n"
        )
        try:
            _vault.write_file(skill_path, skill_md)
            created_skills.append(s_name)
        except Exception:
            log.exception("team import: failed to write skill %s", s_name)

    # Attach skills to bots (match by name).
    # Note: Project model has no skills field, so skills live only as vault
    # files (skills/<slug>/SKILL.md). Bots reference them by name in persona.
    for _b, _proj_id in zip(bots_in, created_ids):
        pass  # skills are vault files; bot linking via persona text is manual

    # Create teams (rooms).
    created_rooms: list[dict[str, Any]] = []
    for r in rooms_in:
        r_name = str(r.get("name") or team_name)
        r_members = list(r.get("members") or [])
        r_mode = str(r.get("mode") or "lead")
        # Resolve member handles to new bot ids.
        resolved_members: list[str] = []
        for mh in r_members:
            # find by handle
            found = None
            for old_handle, new_handle in handle_map.items():
                if old_handle == mh:
                    found = new_handle
                    break
            if not found:
                found = pstore.get_by_handle(mh)
                if found:
                    found = found.handle
            if found:
                resolved_members.append(found)
        if len(resolved_members) < 1:
            continue
        # Use coordinator teams tool equivalent via _create_team_impl
        from ..server.routes.bots import _create_team_impl

        # We need a session for the team; create one via set_team on a new session.
        from ..server.session_store import SessionStore

        store = SessionStore(sessions_db())
        new_session = store.create()
        try:
            _create_team_impl(
                store,
                {
                    "name": r_name,
                    "members": resolved_members,
                    "mode": r_mode,
                },
            )
            created_rooms.append(
                {"name": r_name, "session_id": new_session.id, "members": resolved_members, "mode": r_mode}
            )
        except Exception:
            log.exception("team import: failed to create team %s", r_name)

    # Routines: create paused workflows only (never enabled on import).
    created_routines: list[dict[str, Any]] = []
    for rt in routines_in:
        # Routines arrive paused; store as a workflow with enabled=false.
        rt_name = str(rt.get("name") or "Imported Routine")
        rt_type = str(rt.get("type") or "schedule")
        rt_cron = str(rt.get("cron") or "")
        wf_path = f"workflows/{_slugify(rt_name)}.md"
        wf_fm = {
            "workflow-plugin": "basic",
            "enabled": False,  # always paused on import
            "title": rt_name,
            "triggers": [
                {"id": str(uuid.uuid4().hex[:8]), "type": rt_type, "cron": rt_cron}
                if rt_type == "schedule" and rt_cron
                else {"id": str(uuid.uuid4().hex[:8]), "type": rt_type}
            ],
        }
        wf_md = f"---\n{yaml.dump(wf_fm, default_flow_style=False)}---\n# {rt_name}\n\nImported routine — paused until you enable it.\n"
        try:
            _vault.write_file(wf_path, wf_md)
            created_routines.append({"name": rt_name, "path": wf_path, "paused": True})
        except Exception:
            log.exception("team import: failed to write workflow %s", wf_path)

    return {
        "ok": True,
        "action": "import",
        "path": path,
        "team": {
            "name": team_name,
            "bots": len(created_ids),
            "skills": len(created_skills),
            "rooms": len(created_rooms),
            "routines": len(created_routines),
            "routines_paused": True,
        },
        "bot_ids": created_ids,
        "room_ids": [r["session_id"] for r in created_rooms],
        "routines": created_routines,
    }


# ── tool handlers ──────────────────────────────────────────────────────

async def handle_team_package(
    args: dict[str, Any],
    engine_getter: Callable[[], Any] | None,
) -> str:
    action = args.get("action")
    if action not in ("export", "import"):
        return json.dumps({"ok": False, "error": "invalid action (export|import)"})

    pstore = _bot_store()

    if action == "export":
        session_id = args.get("session_id", "")
        if not session_id:
            return json.dumps({"ok": False, "error": "session_id required for export"})
        output_path = args.get("path")
        result = await _export_team(session_id, output_path, None, pstore)
        return json.dumps(result)

    # import
    path = args.get("path", "")
    if not path:
        return json.dumps({"ok": False, "error": "path required for import"})
    name_override = args.get("name")
    confirmed = bool(args.get("confirmed", False))
    result = await _import_team(path, name_override, confirmed, pstore)
    return json.dumps(result)
