"""The ``site_credentials`` tool — ask the user for site logins, store them
encrypted, and fill them into browsers.

Capture rides the same pending-future rails as ``ask_user`` / ``page``:
the handler publishes a ``user_request`` form event (masked password
field) and the user answers through the generic ``POST /chat/{sid}/respond``
endpoint. Unlike ``ask_user`` forms it **never parks** — a parked
credential form would persist its answer to the sessions DB and replay it
into the LLM context on resume. Instead the request waits synchronously
(up to 10 minutes) and simply times out when nobody answers.

Fill resolves the credential **server-side** and hands it straight to the
target browser surface — CDP (chrome-devtools debug Chrome) or the
``page`` tool's hidden ``fill_login`` action (user's real tab via the
Chrome side panel). The password is never part of tool arguments, tool
results, or the transcript.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from .context import CURRENT_SESSION_ID
from .llm import ToolSpec

SITE_CREDENTIALS_TOOL = ToolSpec(
    name="site_credentials",
    description=(
        "Manage saved site logins (username + password, Fernet-encrypted at "
        "rest in ~/.nexus/site_credentials.db) and fill them into browsers. "
        "You NEVER see a password: 'save' opens a masked form for the user "
        "and stores the answer encrypted; 'fill' resolves the credential "
        "server-side and types it into the login form. Actions: "
        "'list' (saved sites + usernames), 'save' (prompt the user for a "
        "login — pass a short reason), 'fill' (log into the current page: "
        "surface='cdp' targets the chrome-devtools debug Chrome on port "
        "9223, surface='page' targets the user's real tab via the Chrome "
        "side panel), 'delete'. For browser login flows ALWAYS prefer this "
        "over asking the user to type a password into chat."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "save", "fill", "delete"],
                "description": "What to do with the site credential store.",
            },
            "site": {
                "type": "string",
                "description": (
                    "Site domain or URL, e.g. 'github.com' or "
                    "'https://github.com/login' — normalized to the bare host."
                ),
            },
            "surface": {
                "type": "string",
                "enum": ["cdp", "page"],
                "description": (
                    "fill: which browser to fill. 'cdp' = chrome-devtools "
                    "debug Chrome (default); 'page' = the user's real tab "
                    "through the Chrome side panel."
                ),
            },
            "reason": {
                "type": "string",
                "description": "save: one short sentence of context shown to the user in the form.",
            },
            "overwrite": {
                "type": "boolean",
                "description": "save: re-prompt even when a login is already stored for the site.",
            },
            "selectors": {
                "type": "object",
                "description": (
                    "fill: optional CSS selectors {user, pass} when the "
                    "login form is not auto-detected."
                ),
            },
            "submit": {
                "type": "boolean",
                "description": "fill: submit the form after filling (default true).",
            },
            "tab": {
                "type": "string",
                "description": "fill: url/title substring to pick the tab (cdp or page surface).",
            },
            "port": {
                "type": "integer",
                "description": "fill with surface=cdp: debug Chrome port (default 9223).",
            },
        },
        "required": ["action"],
    },
)

# How long the save form stays open. No parking — a parked credential
# answer would be persisted and replayed into the LLM context.
_SAVE_TIMEOUT_SECONDS = 600


def _json(ok: bool, **rest: Any) -> str:
    return json.dumps({"ok": ok, **rest}, ensure_ascii=False)


class SiteCredentialsHandler:
    """Implements the ``site_credentials`` tool actions."""

    def __init__(
        self,
        *,
        session_store: Any,
        ask_user: Any | None = None,
        page: Any | None = None,
    ) -> None:
        self._sessions = session_store
        self._ask_user = ask_user
        self._page = page

    async def invoke(self, args: dict[str, Any]) -> str:
        from .. import site_credentials as store

        action = args.get("action")
        if action == "list":
            return _json(True, sites=store.list_sites())
        if action == "save":
            return await self._save(args)
        if action == "fill":
            return await self._fill(args)
        if action == "delete":
            return await self._delete(args)
        return _json(False, error=f"unknown action {action!r}; expected list/save/fill/delete")

    # ── save ─────────────────────────────────────────────────────────────

    async def _save(self, args: dict[str, Any]) -> str:
        from .. import site_credentials as store

        site = store.normalize_site(args.get("site") or "")
        if not site:
            return _json(False, error="'site' is required (domain or URL)")
        if store.exists(site) and not args.get("overwrite"):
            return _json(
                False,
                error=(
                    f"a login for {site} is already saved — use action='fill', "
                    "or pass overwrite=true to replace it"
                ),
                site=site,
            )

        session_id = CURRENT_SESSION_ID.get()
        if session_id is None or self._sessions is None:
            return _json(
                False,
                error="site_credentials save needs a live chat session (HITL channel)",
            )

        from ..server.events import SessionEvent

        reason = args.get("reason") if isinstance(args.get("reason"), str) else ""
        fields = [
            {
                "name": "username",
                "label": "Username / email",
                "kind": "text",
                "required": True,
                "secret": False,
            },
            {
                "name": "password",
                "label": "Password",
                "kind": "text",
                "required": True,
                "secret": True,
            },
        ]
        description = (
            f"Stored Fernet-encrypted in ~/.nexus/site_credentials.db and used "
            f"only to fill login forms for {site}. The agent never sees the "
            "password."
        )
        if reason:
            description = f"{reason}\n\n{description}"

        request_id = uuid.uuid4().hex
        fut = self._sessions.register_pending(session_id, request_id)
        self._sessions.publish(
            session_id,
            SessionEvent(
                kind="user_request",
                data={
                    "request_id": request_id,
                    "prompt": f"Site login for {site}",
                    "kind": "form",
                    "choices": None,
                    "default": None,
                    "timeout_seconds": _SAVE_TIMEOUT_SECONDS,
                    "fields": fields,
                    "form_title": f"Save site login: {site}",
                    "form_description": description,
                },
            ),
        )
        try:
            raw = await asyncio.wait_for(
                asyncio.shield(fut), timeout=_SAVE_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            self._sessions.cancel_pending(session_id, request_id)
            self._sessions.publish(
                session_id,
                SessionEvent(
                    kind="user_request_cancelled",
                    data={"request_id": request_id, "reason": "timeout"},
                ),
            )
            return _json(False, error=f"timed out waiting for the {site} login form")
        except asyncio.CancelledError:
            self._sessions.cancel_pending(session_id, request_id)
            self._sessions.publish(
                session_id,
                SessionEvent(
                    kind="user_request_cancelled",
                    data={"request_id": request_id, "reason": "cancelled"},
                ),
            )
            raise

        answer: Any = raw
        if isinstance(raw, str):
            try:
                answer = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                answer = raw
        if not isinstance(answer, dict):
            return _json(False, error="credential form did not return a form answer")
        username = answer.get("username")
        password = answer.get("password")
        if not isinstance(username, str) or not username:
            return _json(False, error="missing username in form answer")
        if not isinstance(password, str) or not password:
            return _json(False, error="missing password in form answer")

        saved = store.save(site, username, password)
        return _json(
            True,
            site=saved.site,
            username=saved.username,
            note="saved encrypted; the password was not exposed to you",
        )

    # ── fill ─────────────────────────────────────────────────────────────

    async def _fill(self, args: dict[str, Any]) -> str:
        from .. import site_credentials as store

        site = store.normalize_site(args.get("site") or "")
        if not site:
            return _json(False, error="'site' is required (domain or URL)")
        cred = store.get(site)
        if cred is None:
            return _json(
                False,
                error=f"no saved login for {site} — call action='save' first",
                site=site,
            )

        selectors = args.get("selectors") if isinstance(args.get("selectors"), dict) else {}
        user_sel = selectors.get("user") if isinstance(selectors.get("user"), str) else None
        pass_sel = selectors.get("pass") if isinstance(selectors.get("pass"), str) else None
        submit = args.get("submit")
        submit = True if submit is None else bool(submit)
        tab = args.get("tab") if isinstance(args.get("tab"), str) else None
        surface = args.get("surface") or "cdp"

        if surface == "page":
            return await self._fill_via_page(
                cred, user_sel=user_sel, pass_sel=pass_sel, submit=submit, tab=tab
            )
        if surface == "cdp":
            return await self._fill_via_cdp(
                cred,
                user_sel=user_sel,
                pass_sel=pass_sel,
                submit=submit,
                tab=tab,
                port=args.get("port"),
            )
        return _json(False, error=f"unknown surface {surface!r}; expected 'cdp' or 'page'")

    async def _fill_via_page(
        self,
        cred: Any,
        *,
        user_sel: str | None,
        pass_sel: str | None,
        submit: bool,
        tab: str | None,
    ) -> str:
        if self._page is None:
            return _json(
                False,
                error="page handler not wired — surface='page' needs the Chrome side panel stack",
            )
        result_text = await self._page.invoke(
            {
                "action": "fill_login",
                "site": cred.site,
                "username": cred.username,
                "password": cred.password,
                "user_selector": user_sel,
                "pass_selector": pass_sel,
                "submit": submit,
                "tab": tab,
            }
        )
        try:
            result = json.loads(result_text)
        except (json.JSONDecodeError, TypeError):
            result = {"ok": False, "error": "page handler returned an unparseable result"}
        if result.get("ok"):
            from .. import site_credentials as store

            store.mark_used(cred.site)
        return json.dumps(result, ensure_ascii=False)

    async def _fill_via_cdp(
        self,
        cred: Any,
        *,
        user_sel: str | None,
        pass_sel: str | None,
        submit: bool,
        tab: str | None,
        port: Any,
    ) -> str:
        from ..cdp_fill import DEFAULT_CDP_PORT, cdp_fill_login

        try:
            cdp_port = int(port) if port else DEFAULT_CDP_PORT
        except (TypeError, ValueError):
            cdp_port = DEFAULT_CDP_PORT
        try:
            result = await cdp_fill_login(
                site=cred.site,
                username=cred.username,
                password=cred.password,
                user_selector=user_sel,
                pass_selector=pass_sel,
                submit=submit,
                port=cdp_port,
                tab=tab,
            )
        except RuntimeError as exc:
            return _json(False, error=str(exc))
        if result.get("ok"):
            from .. import site_credentials as store

            store.mark_used(cred.site)
        return json.dumps(result, ensure_ascii=False)

    # ── delete ───────────────────────────────────────────────────────────

    async def _delete(self, args: dict[str, Any]) -> str:
        from .. import site_credentials as store

        site = store.normalize_site(args.get("site") or "")
        if not site:
            return _json(False, error="'site' is required (domain or URL)")
        cred = store.get(site)
        if cred is None:
            return _json(False, error=f"no saved login for {site}", site=site)

        if self._ask_user is not None:
            result = await self._ask_user.invoke(
                {
                    "prompt": (
                        f"Delete the saved login for {site} "
                        f"({cred.username})? This cannot be undone."
                    ),
                    "kind": "confirm",
                }
            )
            answer = getattr(result, "answer", None)
            if not result.ok or result.timed_out:
                return _json(False, error="delete cancelled (confirm failed or timed out)")
            if answer not in (True, "true", "True", "yes", "Yes"):
                return _json(False, error="delete cancelled by user", site=site)

        deleted = store.delete(site)
        return _json(True, site=site, deleted=deleted)
