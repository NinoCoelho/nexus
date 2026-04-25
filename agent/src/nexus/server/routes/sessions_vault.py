"""Session vault/import routes: POST /sessions/{sid}/to-vault, POST /sessions/import.

Split from sessions.py to keep both files under 300 lines.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..deps import get_agent, get_sessions, get_app_state
from ..session_store import SessionStore
from ...agent.loop import Agent
from .sessions import _session_markdown

log = logging.getLogger(__name__)

router = APIRouter()


@router.post("/sessions/{session_id}/to-vault")
async def session_to_vault(
    session_id: str,
    body: dict,
    store: SessionStore = Depends(get_sessions),
    a: Agent = Depends(get_agent),
    app_state: dict[str, Any] = Depends(get_app_state),
) -> dict:
    """Save a session into the vault.

    Body: {"mode": "raw" | "summary", "path"?: str}

    - raw: dumps the session as markdown (same shape as the export endpoint)
      into `sessions/<slug>-<id8>.md` under the vault.
    - summary: calls the default LLM to produce a concise note and writes it
      to `notes/session-<slug>-<id8>.md` with YAML frontmatter tagging it.
    """
    import re
    from datetime import datetime, timezone
    from ... import vault as _vault
    from ...agent.llm import ChatMessage, Role

    mode = (body.get("mode") or "raw").lower()
    explicit_path: str | None = body.get("path")
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"session {session_id!r} not found")

    slug = re.sub(r"[^a-z0-9]+", "-", (session.title or "session").lower()).strip("-")[:40] or "session"
    id8 = session.id[:8]

    if mode == "raw":
        md = _session_markdown(session, store, include_frontmatter=True)
        path = explicit_path or f"sessions/{slug}-{id8}.md"
        try:
            _vault.write_file(path, md)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
        return {"mode": "raw", "path": path, "bytes": len(md.encode("utf-8"))}

    if mode == "summary":
        # Render the conversation (no frontmatter — just the exchange).
        convo = _session_markdown(session, store, include_frontmatter=False)
        if not convo.strip():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="session has no user/assistant turns")
        sys_prompt = (
            "You are summarizing a chat session for a personal knowledge base. Output ONLY markdown — "
            "no preamble like 'Here is the summary'. Keep it compact (200–500 words). Structure:\n"
            "## Goal\n(1–2 sentences)\n"
            "## Key decisions\n(bullets)\n"
            "## Actions / next steps\n(bullets, imperative)\n"
            "## References\n(file paths, URLs, commands mentioned — verbatim)\n"
            "Be specific, skip pleasantries, preserve exact names and numbers."
        )
        user_prompt = f"Session title: {session.title}\n\n{convo}"
        # Route through the same provider resolver the agent loop uses so
        # the upstream model name is passed correctly (OpenAI-compat and
        # Anthropic both require it). Falls back to the env-var provider
        # if no registry is wired.
        cfg = app_state.get("cfg")
        default_model = cfg.agent.default_model if cfg and cfg.agent else None
        try:
            provider, upstream = a._resolve_provider(default_model)
            resp = await provider.chat(
                messages=[
                    ChatMessage(role=Role.SYSTEM, content=sys_prompt),
                    ChatMessage(role=Role.USER, content=user_prompt),
                ],
                tools=[],
                model=upstream,
            )
            summary = (resp.content or "").strip()
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"summary failed: {exc}")
        if not summary:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="empty summary from model")
        now_iso = datetime.now(tz=timezone.utc).isoformat()
        fm = (
            "---\n"
            f"source: chat-session\n"
            f"session_id: {session.id}\n"
            f"title: {json.dumps(session.title)}\n"
            f"summarized_at: {now_iso}\n"
            f"tags: [session-summary]\n"
            "---\n\n"
        )
        note = fm + summary + ("\n" if not summary.endswith("\n") else "")
        path = explicit_path or f"notes/session-{slug}-{id8}.md"
        try:
            _vault.write_file(path, note)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
        return {"mode": "summary", "path": path, "length": len(summary)}

    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"unknown mode: {mode!r}")


@router.post("/sessions/import")
async def import_session(
    request: Request,
    store: SessionStore = Depends(get_sessions),
) -> dict:
    from datetime import datetime, timezone
    import re
    import uuid as _uuid
    from ...agent.llm import ChatMessage, Role

    content_type = request.headers.get("content-type", "")

    if "multipart/form-data" in content_type:
        form = await request.form()
        file_field = form.get("file")
        if file_field is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="`file` field required")
        markdown = (await file_field.read()).decode("utf-8")  # type: ignore[union-attr]
    else:
        body = await request.json()
        markdown = body.get("markdown", "")
        if not markdown:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="`markdown` required")

    # Parse optional YAML frontmatter.
    fm: dict[str, str] = {}
    body_text = markdown
    fm_match = re.match(r"^---\n(.*?)\n---\n?", markdown, re.DOTALL)
    if fm_match:
        for line in fm_match.group(1).splitlines():
            if ": " in line:
                k, _, v = line.partition(": ")
                fm[k.strip()] = v.strip()
        body_text = markdown[fm_match.end():]

    # Determine title.
    title = "Imported session"
    if "title" in fm:
        try:
            title = json.loads(fm["title"])
        except Exception:
            title = fm["title"].strip('"\'')
    else:
        h1 = re.search(r"^#\s+(.+)$", body_text, re.MULTILINE)
        if h1:
            title = h1.group(1).strip()

    context: str | None = None
    if "context" in fm:
        raw_ctx = fm["context"].strip()
        if raw_ctx and raw_ctx.lower() != "null":
            try:
                context = json.loads(raw_ctx)
            except Exception:
                context = raw_ctx

    # Assign id — avoid clobbering existing sessions.
    new_id = _uuid.uuid4().hex
    if "nexus_session_id" in fm:
        candidate = fm["nexus_session_id"].strip()
        if candidate and store.get(candidate) is None:
            new_id = candidate

    # Reconstruct messages from level-2 headings.
    messages: list[ChatMessage] = []
    now = int(datetime.now(tz=timezone.utc).timestamp())
    sections = re.split(r"\n## (You|Nexus) · [^\n]*\n", body_text)
    # sections[0] is text before first heading (ignored); then alternating label/content pairs.
    i = 1
    while i + 1 < len(sections):
        speaker = sections[i].strip()
        content = sections[i + 1].strip()
        i += 2
        if not content:
            continue
        role = Role.USER if speaker == "You" else Role.ASSISTANT
        messages.append(ChatMessage(role=role, content=content))

    # Insert into store via the public import_session method.
    store.import_session(new_id, title, context, messages, now)

    return {"id": new_id, "title": title, "imported_message_count": len(messages)}
