"""Role-based permission tiers for multi-user mode.

Maps each user role (admin / member / viewer) to a set of allowed agent
tools and API capabilities.  In single-user mode the admin tier is used
implicitly — no permission checks run.

The tiers are intentionally coarse:

- **admin** — full access (same as single-user mode)
- **member** — can chat, read/write vault, use tools; cannot modify server
  config, manage users, or use destructive tools (terminal, skill_manage
  delete)
- **viewer** — read-only chat and vault browsing; no write tools
"""

from __future__ import annotations


from loom.permissions import AgentPermissions

from .user_store.models import Role

# ── Tool allowlists per role ────────────────────────────────────────────────
# None means "all tools" (admin).  An explicit set means "only these".
# Tool names correspond to the `name` field on each tool's ToolSpec.

_WRITER_TOOLS = frozenset({
    "vault_write",
    "vault_write_bytes",
    "vault_delete",
    "vault_move",
    "vault_create_folder",
    "memory_write",
    "kanban_manage",
    "dashboard_manage",
    "datatable_manage",
    "calendar_manage",
    "dispatch_card",
    "skill_manage",
})

_DESTRUCTIVE_TOOLS = frozenset({
    "terminal",
})

_ADMIN_ONLY_TOOLS = frozenset({
    "skill_manage",  # delete/create skills
})

_MEMBER_TOOLS: frozenset[str] | None = None  # None = all except _ADMIN_ONLY_TOOLS + _DESTRUCTIVE_TOOLS

_VIEWER_TOOLS: frozenset[str] | None = frozenset({
    "vault_read",
    "vault_list",
    "vault_search",
    "kanban_query",
    "skill_view",
    "memory_read",
    "http_call",
    "ask_user",
    "show_kanban",
    "show_dashboard_widget",
    "show_data_table",
})

# ── AgentPermissions per role ───────────────────────────────────────────────

_ADMIN_PERMS = AgentPermissions(
    soul_writable=False,
    identity_writable=False,
    user_writable=True,
    skills_creatable=True,
    skills_editable=True,
    skills_deletable=True,
    memory_writable=True,
    vault_writable=True,
    terminal_allowed=True,
    http_allowed=True,
    delegate_allowed=True,
)

_MEMBER_PERMS = AgentPermissions(
    soul_writable=False,
    identity_writable=False,
    user_writable=True,
    skills_creatable=True,
    skills_editable=True,
    skills_deletable=False,
    memory_writable=True,
    vault_writable=True,
    terminal_allowed=False,
    http_allowed=True,
    delegate_allowed=True,
)

_VIEWER_PERMS = AgentPermissions(
    soul_writable=False,
    identity_writable=False,
    user_writable=False,
    skills_creatable=False,
    skills_editable=False,
    skills_deletable=False,
    memory_writable=False,
    vault_writable=False,
    terminal_allowed=False,
    http_allowed=True,
    delegate_allowed=False,
)


def permissions_for_role(role: Role | None) -> AgentPermissions:
    """Return AgentPermissions for the given role.

    ``None`` (single-user mode) returns admin permissions.
    """
    if role is None or role == "admin":
        return _ADMIN_PERMS
    if role == "member":
        return _MEMBER_PERMS
    if role == "viewer":
        return _VIEWER_PERMS
    return _MEMBER_PERMS


def allowed_tools_for_role(role: Role | None) -> frozenset[str] | None:
    """Return the set of allowed tool names, or None for "all tools".

    Used to filter the tool list sent to the LLM on each turn.
    """
    if role is None or role == "admin":
        return None
    if role == "member":
        return None  # members get all tools; destructive ones are gated by AgentPermissions
    if role == "viewer":
        return _VIEWER_TOOLS
    return None


def can_write_vault(role: Role | None) -> bool:
    return role in (None, "admin", "member")


def can_manage_skills(role: Role | None) -> bool:
    return role in (None, "admin")


def can_manage_server(role: Role | None) -> bool:
    return role in (None, "admin")
