"""FastAPI application factory for Nexus."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from ..agent.llm import LLMTransportError, MalformedOutputError
from ..agent.loop import Agent
from ..skills.registry import SkillRegistry
from .schemas import ChatReply, ChatRequest, Health, SkillDetail, SkillInfo
from .session_store import SessionStore

log = logging.getLogger(__name__)


def create_app(
    *,
    agent: Agent,
    registry: SkillRegistry,
    sessions: SessionStore | None = None,
    nexus_cfg: Any | None = None,
    provider_registry: Any | None = None,
) -> FastAPI:
    sessions = sessions or SessionStore()
    _state = {"cfg": nexus_cfg, "prov_reg": provider_registry}

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await agent.aclose()

    app = FastAPI(title="nexus", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"http://localhost:\d+",
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def get_agent() -> Agent:
        return agent

    def get_sessions() -> SessionStore:
        return sessions

    # ── existing routes ────────────────────────────────────────────────────────

    @app.get("/health", response_model=Health)
    async def health() -> Health:
        return Health()

    @app.get("/skills", response_model=list[SkillInfo])
    async def list_skills() -> list[SkillInfo]:
        return [
            SkillInfo(name=s.name, description=s.description, trust=s.trust)
            for s in registry.list()
        ]

    @app.get("/skills/{name}", response_model=SkillDetail)
    async def get_skill(name: str) -> SkillDetail:
        try:
            s = registry.get(name)
        except KeyError:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no such skill: {name!r}")
        return SkillDetail(name=s.name, description=s.description, trust=s.trust, body=s.body)

    @app.post("/chat", response_model=ChatReply)
    async def chat(
        req: ChatRequest,
        a: Agent = Depends(get_agent),
        store: SessionStore = Depends(get_sessions),
    ) -> ChatReply:
        session = store.get_or_create(req.session_id, context=req.context)
        try:
            turn = await a.run_turn(
                req.message,
                history=session.history,
                context=session.context,
            )
        except LLMTransportError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
        except MalformedOutputError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
        store.replace_history(session.id, turn.messages)
        # Fold the turn's usage into the session — see session_store.bump_usage.
        store.bump_usage(
            session.id,
            model=turn.model,
            input_tokens=turn.input_tokens,
            output_tokens=turn.output_tokens,
            tool_calls=turn.tool_calls,
        )
        return ChatReply(
            session_id=session.id,
            reply=turn.reply,
            trace=turn.trace,
            skills_touched=turn.skills_touched,
            iterations=turn.iterations,
        )

    @app.post("/chat/stream")
    async def chat_stream_route(
        req: ChatRequest,
        a: Agent = Depends(get_agent),
        store: SessionStore = Depends(get_sessions),
    ) -> StreamingResponse:
        session = store.get_or_create(req.session_id, context=req.context)

        async def event_generator() -> AsyncIterator[str]:
            final_messages = None
            try:
                async for event in a.run_turn_stream(
                    req.message,
                    history=session.history,
                    context=session.context,
                    session_id=session.id,
                ):
                    etype = event.get("type")

                    if etype == "delta":
                        yield f"event: delta\ndata: {json.dumps({'text': event['text']})}\n\n"

                    elif etype in ("tool_exec_start", "tool_exec_result"):
                        payload: dict[str, Any] = {"name": event.get("name", "")}
                        if "args" in event:
                            payload["args"] = event["args"]
                        if "result_preview" in event:
                            payload["result_preview"] = event["result_preview"]
                        yield f"event: tool\ndata: {json.dumps(payload)}\n\n"

                    elif etype == "done":
                        final_messages = event.get("messages")
                        usage = event.get("usage") or {}
                        # Persist the turn's usage onto the session so
                        # /insights can roll it up later. Done here (not
                        # in `finally`) because the done event is only
                        # emitted on successful completion of the turn.
                        try:
                            store.bump_usage(
                                session.id,
                                model=usage.get("model"),
                                input_tokens=int(usage.get("input_tokens") or 0),
                                output_tokens=int(usage.get("output_tokens") or 0),
                                tool_calls=int(usage.get("tool_calls") or 0),
                            )
                        except Exception:  # noqa: BLE001 — best-effort
                            log.exception("bump_usage failed")
                        done_payload = {
                            "session_id": event.get("session_id") or session.id,
                            "reply": event.get("reply", ""),
                            "trace": event.get("trace", []),
                            "skills_touched": event.get("skills_touched", []),
                            "iterations": event.get("iterations", 0),
                            "usage": usage,
                        }
                        yield f"event: done\ndata: {json.dumps(done_payload)}\n\n"

                    elif etype == "error":
                        # Mid-stream structured error from the agent loop
                        # (e.g. an upstream failure after content was already
                        # streamed, so retry was impossible). Forward the
                        # classifier's fields so the UI can show a richer
                        # message without re-parsing the detail string.
                        err_payload = {
                            "detail": event.get("detail", ""),
                            "reason": event.get("reason"),
                            "retryable": event.get("retryable"),
                            "status_code": event.get("status_code"),
                        }
                        yield f"event: error\ndata: {json.dumps(err_payload)}\n\n"

            except (LLMTransportError, MalformedOutputError) as exc:
                # Classify so the client gets a readable summary on top of
                # the raw detail (e.g. "Provider rate limit — retrying with
                # backoff." vs. the raw "HTTP 429: ..." body).
                detail = str(exc)
                reason = None
                retryable = None
                status_code = getattr(exc, "status_code", None)
                try:
                    from ..error_classifier import classify_api_error
                    classified = classify_api_error(exc)
                    reason = classified.reason.value
                    retryable = classified.retryable
                    if classified.user_facing_summary:
                        detail = f"{classified.user_facing_summary} ({detail})"
                except Exception:
                    pass
                err_payload = {
                    "detail": detail,
                    "reason": reason,
                    "retryable": retryable,
                    "status_code": status_code,
                }
                yield f"event: error\ndata: {json.dumps(err_payload)}\n\n"
            except Exception as exc:
                # Catch-all so an unexpected error never leaves the client
                # with ERR_INCOMPLETE_CHUNKED_ENCODING. Emit a proper
                # error frame then a terminator done so the client can
                # unwind its UI (flip thinking off, show the error).
                log.exception("chat_stream crashed")
                yield f"event: error\ndata: {json.dumps({'detail': f'{type(exc).__name__}: {exc}'})}\n\n"
                yield f"event: done\ndata: {json.dumps({'session_id': session.id, 'reply': '', 'trace': [], 'skills_touched': [], 'iterations': 0})}\n\n"
            finally:
                if final_messages is not None:
                    store.replace_history(session.id, final_messages)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/sessions")
    async def list_sessions(
        limit: int = 50,
        store: SessionStore = Depends(get_sessions),
    ) -> list[dict]:
        summaries = store.list(limit=limit)
        return [
            {
                "id": s.id,
                "title": s.title,
                "created_at": s.created_at,
                "updated_at": s.updated_at,
                "message_count": s.message_count,
            }
            for s in summaries
        ]

    @app.get("/sessions/{session_id}")
    async def get_session(
        session_id: str,
        store: SessionStore = Depends(get_sessions),
    ) -> dict:
        session = store.get(session_id)
        if session is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"session {session_id!r} not found")
        ts_list = getattr(session, "_message_timestamps", []) or []
        from datetime import datetime, timezone
        def _iso(ts: int | None) -> str | None:
            if ts is None:
                return None
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        return {
            "id": session.id,
            "title": session.title,
            "context": session.context,
            "messages": [
                {
                    "role": m.role,
                    "content": m.content,
                    "tool_calls": [tc.model_dump() for tc in m.tool_calls] if m.tool_calls else None,
                    "tool_call_id": m.tool_call_id,
                    "created_at": _iso(ts_list[i] if i < len(ts_list) else None),
                }
                for i, m in enumerate(session.history)
            ],
        }

    @app.patch("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def rename_session(
        session_id: str,
        body: dict,
        store: SessionStore = Depends(get_sessions),
    ) -> None:
        title = body.get("title")
        if title is not None:
            store.rename(session_id, title)

    @app.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_session(
        session_id: str,
        store: SessionStore = Depends(get_sessions),
    ) -> None:
        store.delete(session_id)

    @app.get("/insights")
    async def get_insights(
        days: int = 30,
        store: SessionStore = Depends(get_sessions),
    ) -> dict[str, Any]:
        """Return a usage analytics report for the last ``days`` days.

        Clamps ``days`` into ``[1, 365]`` to keep aggregation cheap.
        """
        from ..insights import InsightsEngine
        days = max(1, min(int(days), 365))
        engine = InsightsEngine(store._db_path)
        return engine.generate(days=days)

    @app.get("/sessions/{session_id}/export")
    async def export_session(
        session_id: str,
        store: SessionStore = Depends(get_sessions),
    ) -> StreamingResponse:
        from datetime import datetime, timezone
        import re

        session = store.get(session_id)
        if session is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"session {session_id!r} not found")

        # Gather session-level timestamps from the DB.
        with store._connect() as conn:
            row = conn.execute(
                "SELECT created_at, updated_at FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        created_at_ts = row["created_at"] if row else 0
        updated_at_ts = row["updated_at"] if row else 0

        def _iso(ts: int) -> str:
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

        # Build frontmatter (hand-rolled, no nested objects).
        context_val = session.context
        if context_val is None:
            context_yaml = "null"
        else:
            context_yaml = json.dumps(context_val)

        title_yaml = json.dumps(session.title)
        lines: list[str] = [
            "---",
            f"nexus_session_id: {session.id}",
            f"title: {title_yaml}",
            f"created_at: {_iso(created_at_ts)}",
            f"updated_at: {_iso(updated_at_ts)}",
            f"context: {context_yaml}",
            "---",
            "",
        ]

        ts_list: list[int] = getattr(session, "_message_timestamps", []) or []

        for i, msg in enumerate(session.history):
            role = str(msg.role.value if hasattr(msg.role, "value") else msg.role)
            # Skip tool/system messages and empty content.
            if role not in ("user", "assistant"):
                continue
            content = (msg.content or "").strip()
            if not content:
                continue
            msg_ts = ts_list[i] if i < len(ts_list) else created_at_ts
            label = "You" if role == "user" else "Nexus"
            lines.append(f"## {label} · {_iso(msg_ts)}")
            lines.append("")
            lines.append(content)
            lines.append("")

        markdown = "\n".join(lines)

        # Build a safe filename slug from the title.
        slug = re.sub(r"[^a-z0-9]+", "-", session.title.lower()).strip("-")[:40]
        id8 = session.id[:8]
        filename = f"session-{slug}-{id8}.md"

        return StreamingResponse(
            iter([markdown]),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    def _session_markdown(session: Any, include_frontmatter: bool = True) -> str:
        """Render a session as markdown. Shared between export and to-vault.
        Uses the `sessions` closure (not a per-request `store` name)."""
        from datetime import datetime, timezone
        with sessions._connect() as conn:
            row = conn.execute(
                "SELECT created_at, updated_at FROM sessions WHERE id = ?", (session.id,)
            ).fetchone()
        created_at_ts = row["created_at"] if row else 0
        updated_at_ts = row["updated_at"] if row else 0

        def _iso(ts: int) -> str:
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

        lines: list[str] = []
        if include_frontmatter:
            context_yaml = "null" if session.context is None else json.dumps(session.context)
            lines += [
                "---",
                f"nexus_session_id: {session.id}",
                f"title: {json.dumps(session.title)}",
                f"created_at: {_iso(created_at_ts)}",
                f"updated_at: {_iso(updated_at_ts)}",
                f"context: {context_yaml}",
                "---",
                "",
            ]

        ts_list: list[int] = getattr(session, "_message_timestamps", []) or []
        for i, msg in enumerate(session.history):
            role = str(msg.role.value if hasattr(msg.role, "value") else msg.role)
            if role not in ("user", "assistant"):
                continue
            content = (msg.content or "").strip()
            if not content:
                continue
            msg_ts = ts_list[i] if i < len(ts_list) else created_at_ts
            label = "You" if role == "user" else "Nexus"
            lines.append(f"## {label} · {_iso(msg_ts)}")
            lines.append("")
            lines.append(content)
            lines.append("")
        return "\n".join(lines)

    @app.post("/sessions/{session_id}/to-vault")
    async def session_to_vault(
        session_id: str,
        body: dict,
        store: SessionStore = Depends(get_sessions),
        a: Agent = Depends(get_agent),
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
        from .. import vault as _vault
        from ..agent.llm import ChatMessage, Role

        mode = (body.get("mode") or "raw").lower()
        explicit_path: str | None = body.get("path")
        session = store.get(session_id)
        if session is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"session {session_id!r} not found")

        slug = re.sub(r"[^a-z0-9]+", "-", (session.title or "session").lower()).strip("-")[:40] or "session"
        id8 = session.id[:8]

        if mode == "raw":
            md = _session_markdown(session, include_frontmatter=True)
            path = explicit_path or f"sessions/{slug}-{id8}.md"
            try:
                _vault.write_file(path, md)
            except (ValueError, OSError) as exc:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
            return {"mode": "raw", "path": path, "bytes": len(md.encode("utf-8"))}

        if mode == "summary":
            # Render the conversation (no frontmatter — just the exchange).
            convo = _session_markdown(session, include_frontmatter=False)
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
            cfg = _state.get("cfg")
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

    @app.post("/sessions/import")
    async def import_session(
        request: "Request",
        store: SessionStore = Depends(get_sessions),
    ) -> dict:
        from datetime import datetime, timezone
        import re
        import uuid as _uuid
        from ..agent.llm import ChatMessage, Role

        content_type = request.headers.get("content-type", "")

        if "multipart/form-data" in content_type:
            from fastapi import UploadFile, Form
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

        # Insert into store directly.
        with store._lock, store._connect() as conn:
            conn.execute(
                "INSERT INTO sessions (id, title, context, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (new_id, title, context, now, now),
            )
            rows = [
                (new_id, seq, msg.role, msg.content or "", None, None, now)
                for seq, msg in enumerate(messages)
            ]
            conn.executemany(
                "INSERT INTO messages (session_id, seq, role, content, tool_calls, tool_call_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

        return {"id": new_id, "title": title, "imported_message_count": len(messages)}

    # ── vault routes ───────────────────────────────────────────────────────────

    @app.get("/vault/tree")
    async def vault_tree() -> list[dict]:
        from ..vault import list_tree
        entries = list_tree()
        return [{"path": e.path, "type": e.type, "size": e.size, "mtime": e.mtime} for e in entries]

    @app.get("/vault/tags")
    async def vault_list_tags() -> list[dict]:
        from .. import vault_index
        if vault_index.is_empty():
            vault_index.rebuild_from_disk()
        return vault_index.list_tags()

    @app.get("/vault/tags/{tag}")
    async def vault_files_for_tag(tag: str) -> dict:
        from .. import vault_index
        if vault_index.is_empty():
            vault_index.rebuild_from_disk()
        return {"tag": tag, "files": vault_index.files_with_tag(tag)}

    @app.get("/vault/backlinks")
    async def vault_backlinks_endpoint(path: str) -> dict:
        from .. import vault_index
        if vault_index.is_empty():
            vault_index.rebuild_from_disk()
        return {"path": path, "backlinks": vault_index.backlinks(path)}

    @app.get("/vault/file")
    async def vault_read_file(path: str) -> dict:
        from ..vault import read_file
        from .. import vault_index
        try:
            result = read_file(path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
        try:
            if vault_index.is_empty():
                vault_index.rebuild_from_disk()
            result["tags"] = vault_index.tags_for_file(path)
            result["backlinks"] = vault_index.backlinks(path)
        except Exception:
            log.warning("vault_index: failed to attach tags/backlinks", exc_info=True)
        return result

    @app.put("/vault/file", status_code=status.HTTP_204_NO_CONTENT)
    async def vault_write_file(body: dict) -> None:
        from ..vault import write_file
        path = body.get("path", "")
        content = body.get("content", "")
        if not path:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="`path` required")
        try:
            write_file(path, content)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    @app.delete("/vault/file", status_code=status.HTTP_204_NO_CONTENT)
    async def vault_delete_file(path: str) -> None:
        from ..vault import delete
        try:
            delete(path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    @app.post("/vault/folder", status_code=status.HTTP_201_CREATED)
    async def vault_create_folder(body: dict) -> dict:
        from ..vault import create_folder
        path = body.get("path", "")
        if not path:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="`path` required")
        try:
            create_folder(path)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
        return {"path": path}

    @app.get("/vault/search")
    async def vault_search_endpoint(q: str = "", limit: int = 50) -> dict:
        from .. import vault_search
        q = q.strip()
        if not q:
            return {"results": [], "q": q, "count": 0}
        if vault_search.is_empty():
            vault_search.rebuild_from_disk()
        results = vault_search.search(q, limit=limit)
        return {"results": results, "q": q, "count": len(results)}

    @app.post("/vault/reindex")
    async def vault_reindex() -> dict:
        from .. import vault_search
        n = vault_search.rebuild_from_disk()
        return {"indexed": n}

    @app.get("/vault/graph")
    async def vault_graph() -> dict:
        from ..vault_graph import build_graph
        data = build_graph()
        return {
            "nodes": data["nodes"],
            "edges": [{"from": e["from_"], "to": e["to"]} for e in data["edges"]],
            "orphans": data["orphans"],
        }

    @app.post("/vault/move", status_code=status.HTTP_204_NO_CONTENT)
    async def vault_move(body: dict) -> None:
        from ..vault import move
        from_path = body.get("from", "")
        to_path = body.get("to", "")
        if not from_path or not to_path:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="`from` and `to` required")
        try:
            move(from_path, to_path)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    # ── kanban routes ──────────────────────────────────────────────────────────

    @app.get("/kanban/boards")
    async def kanban_list_boards() -> list:
        from ..kanban import list_boards
        return list_boards()

    @app.post("/kanban/boards", status_code=status.HTTP_201_CREATED)
    async def kanban_create_board(body: dict) -> dict:
        from ..kanban import create_board
        name = body.get("name", "")
        if not name:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="`name` required")
        try:
            create_board(name)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
        return {"name": name, "card_count": 0}

    @app.delete("/kanban/boards/{board_name}", status_code=status.HTTP_204_NO_CONTENT)
    async def kanban_delete_board(board_name: str) -> None:
        from ..kanban import delete_board
        try:
            delete_board(board_name)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))

    @app.get("/kanban")
    async def kanban_board(board: str = "default") -> dict:
        from ..kanban import list_cards, list_columns
        return {
            "columns": list_columns(board),
            "cards": [c.to_dict() for c in list_cards(board)],
        }

    @app.post("/kanban/cards", status_code=status.HTTP_201_CREATED)
    async def kanban_create_card(body: dict, board: str = "default") -> dict:
        from ..kanban import create_card
        title = body.get("title", "")
        if not title:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="`title` required")
        card = create_card(
            title=title,
            column=body.get("column", "todo"),
            notes=body.get("notes", ""),
            tags=body.get("tags") or [],
            board=board,
        )
        return card.to_dict()

    @app.patch("/kanban/cards/{card_id}")
    async def kanban_update_card(card_id: str, body: dict, board: str = "default") -> dict:
        from ..kanban import update_card
        updates: dict[str, Any] = {}
        for key in ("title", "notes", "tags", "column"):
            if key in body:
                updates[key] = body[key]
        try:
            card = update_card(card_id, updates, board)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
        return card.to_dict()

    @app.delete("/kanban/cards/{card_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def kanban_delete_card(card_id: str, board: str = "default") -> None:
        from ..kanban import delete_card
        try:
            delete_card(card_id, board)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    @app.post("/kanban/columns", status_code=status.HTTP_201_CREATED)
    async def kanban_create_column(body: dict, board: str = "default") -> dict:
        from ..kanban import create_column
        name = body.get("name", "")
        if not name:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="`name` required")
        create_column(name, board)
        return {"name": name}

    @app.delete("/kanban/columns/{name}", status_code=status.HTTP_204_NO_CONTENT)
    async def kanban_delete_column(name: str, board: str = "default") -> None:
        from ..kanban import delete_column
        try:
            delete_column(name, board)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    # ── config routes ──────────────────────────────────────────────────────────

    def _redact_cfg(cfg: Any) -> dict[str, Any]:
        if cfg is None:
            return {}
        import os
        from ..secrets import get as secrets_get
        out: dict[str, Any] = {
            "agent": cfg.agent.model_dump(),
            "providers": {},
            "models": [m.model_dump() for m in cfg.models],
        }
        for name, p in cfg.providers.items():
            key_source: str | None = None
            if p.type == "ollama":
                key_source = "anonymous"
            elif p.use_inline_key and secrets_get(name):
                key_source = "inline"
            elif p.api_key_env and os.environ.get(p.api_key_env):
                key_source = "env"
            has_key = key_source is not None
            out["providers"][name] = {
                "base_url": p.base_url,
                "key_env": p.api_key_env,
                "has_key": has_key,
                "use_inline_key": p.use_inline_key,
                "type": p.type,
            }
        return out

    def _rebuild_registry(cfg: Any) -> None:
        from ..agent.registry import build_registry
        new_reg = build_registry(cfg)
        _state["prov_reg"] = new_reg
        agent._provider_registry = new_reg
        agent._nexus_cfg = cfg
        _state["cfg"] = cfg

    @app.get("/config")
    async def get_config() -> dict[str, Any]:
        return _redact_cfg(_state["cfg"])

    @app.patch("/config")
    async def patch_config(body: dict[str, Any]) -> dict[str, Any]:
        from ..config_file import load as load_cfg, save as save_cfg, NexusConfig
        cfg = _state["cfg"] or load_cfg()
        raw = cfg.model_dump()
        # Shallow merge for "agent"; NESTED merge for "providers" so a partial
        # edit (e.g. base_url only) doesn't wipe fields like `type` that the
        # client didn't send. "has_key" is a read-only synthesized flag and is
        # never persisted.
        if "agent" in body:
            raw["agent"].update(body["agent"])
        if "providers" in body:
            for pname, patch in body["providers"].items():
                existing = raw["providers"].get(pname, {})
                merged = {**existing, **{k: v for k, v in patch.items() if k != "has_key"}}
                raw["providers"][pname] = merged
        if "models" in body:
            raw["models"] = body["models"]
        new_cfg = NexusConfig(**raw)
        save_cfg(new_cfg)
        _rebuild_registry(new_cfg)
        return _redact_cfg(new_cfg)

    @app.get("/providers")
    async def list_providers() -> list[dict[str, Any]]:
        import os
        from ..secrets import get as secrets_get
        cfg = _state["cfg"]
        if not cfg:
            return []
        result = []
        for name, p in cfg.providers.items():
            key_source: str | None = None
            if p.type == "ollama":
                key_source = "anonymous"
            elif p.use_inline_key and secrets_get(name):
                key_source = "inline"
            elif p.api_key_env and os.environ.get(p.api_key_env):
                key_source = "env"
            result.append({
                "name": name,
                "base_url": p.base_url,
                "has_key": key_source is not None,
                "key_source": key_source,
                "key_env": p.api_key_env,
                "type": p.type,
            })
        return result

    @app.get("/providers/{name}/models")
    async def list_provider_models(name: str) -> dict[str, Any]:
        import os
        import httpx as _httpx
        from ..secrets import get as secrets_get

        cfg = _state["cfg"]
        if not cfg or name not in cfg.providers:
            return {"models": [], "ok": False, "error": f"provider {name!r} not found"}

        p = cfg.providers[name]
        provider_type = p.type or ("anthropic" if name == "anthropic" else "openai_compat")

        try:
            async with _httpx.AsyncClient(timeout=5.0) as client:
                if provider_type == "ollama":
                    base = (p.base_url or "http://localhost:11434").rstrip("/")
                    # Try /api/tags first (native Ollama endpoint)
                    try:
                        r = await client.get(f"{base}/api/tags")
                        if r.status_code == 200:
                            data = r.json()
                            models = [m["name"] for m in data.get("models", [])]
                            return {"models": models, "ok": True, "error": None}
                        elif r.status_code == 404:
                            # Fall back to OpenAI-compat /v1/models
                            r2 = await client.get(f"{base}/v1/models")
                            if r2.status_code == 200:
                                data2 = r2.json()
                                models = [m["id"] for m in data2.get("data", [])]
                                return {"models": models, "ok": True, "error": None}
                            else:
                                return {"models": [], "ok": False, "error": f"HTTP {r2.status_code} from {base}/v1/models"}
                        else:
                            return {"models": [], "ok": False, "error": f"HTTP {r.status_code} from {base}/api/tags"}
                    except _httpx.ConnectError as exc:
                        return {"models": [], "ok": False, "error": f"connection refused — is Ollama running? ({exc})"}

                elif provider_type == "anthropic":
                    # Resolve key
                    api_key = ""
                    if p.use_inline_key:
                        api_key = secrets_get(name) or ""
                    if not api_key and p.api_key_env:
                        api_key = os.environ.get(p.api_key_env, "")
                    if not api_key:
                        return {"models": [], "ok": False, "error": "no API key configured for anthropic — set ANTHROPIC_API_KEY or use nexus providers set-key"}
                    r = await client.get(
                        "https://api.anthropic.com/v1/models",
                        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                    )
                    if r.status_code != 200:
                        return {"models": [], "ok": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
                    data = r.json()
                    models = [m["id"] for m in data.get("data", [])]
                    return {"models": models, "ok": True, "error": None}

                else:
                    # openai_compat
                    if not p.base_url:
                        return {"models": [], "ok": False, "error": "base_url not configured for this provider"}
                    api_key = ""
                    if p.use_inline_key:
                        api_key = secrets_get(name) or ""
                    if not api_key and p.api_key_env:
                        api_key = os.environ.get(p.api_key_env, "")
                    if not api_key:
                        return {"models": [], "ok": False, "error": f"no API key configured — set {p.api_key_env or 'an API key'} or use nexus providers set-key"}
                    headers: dict[str, str] = {"Authorization": f"Bearer {api_key}"}
                    base = p.base_url.rstrip("/")
                    r = await client.get(f"{base}/models", headers=headers)
                    if r.status_code != 200:
                        return {"models": [], "ok": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
                    data = r.json()
                    models = [m["id"] for m in data.get("data", [])]
                    return {"models": models, "ok": True, "error": None}

        except _httpx.TimeoutException:
            return {"models": [], "ok": False, "error": "request timed out (5s)"}
        except Exception as exc:
            return {"models": [], "ok": False, "error": str(exc)}

    @app.post("/providers/{name}/key", status_code=status.HTTP_204_NO_CONTENT)
    async def set_provider_key(name: str, body: dict[str, Any]) -> None:
        from ..config_file import load as load_cfg, save as save_cfg
        from .. import secrets as _secrets
        api_key = body.get("api_key", "")
        if not api_key:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="api_key required")
        cfg = _state["cfg"] or load_cfg()
        if name not in cfg.providers:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"provider {name!r} not found")
        _secrets.set(name, api_key)
        cfg.providers[name].use_inline_key = True
        save_cfg(cfg)
        _rebuild_registry(cfg)

    @app.delete("/providers/{name}/key", status_code=status.HTTP_204_NO_CONTENT)
    async def clear_provider_key(name: str) -> None:
        from ..config_file import load as load_cfg, save as save_cfg
        from .. import secrets as _secrets
        cfg = _state["cfg"] or load_cfg()
        if name not in cfg.providers:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"provider {name!r} not found")
        _secrets.delete(name)
        cfg.providers[name].use_inline_key = False
        save_cfg(cfg)
        _rebuild_registry(cfg)

    @app.get("/models")
    async def list_models() -> list[dict[str, Any]]:
        cfg = _state["cfg"]
        if not cfg:
            return []
        return [m.model_dump() for m in cfg.models]

    @app.post("/models", status_code=status.HTTP_201_CREATED)
    async def add_model(body: dict[str, Any]) -> dict[str, Any]:
        from ..config_file import load as load_cfg, save as save_cfg, ModelEntry, ModelStrengths
        cfg = _state["cfg"] or load_cfg()
        strengths_data = body.pop("strengths", {})
        strengths = ModelStrengths(**strengths_data)
        m = ModelEntry(**body, strengths=strengths)
        cfg.models.append(m)
        # Auto-set as default if nothing is set yet — the DWIM path: a user
        # who just configured their first model expects it to be usable.
        if not cfg.agent.default_model:
            cfg.agent.default_model = m.id
        save_cfg(cfg)
        _rebuild_registry(cfg)
        return m.model_dump()

    @app.delete("/models/{model_id:path}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_model(model_id: str) -> None:
        from ..config_file import load as load_cfg, save as save_cfg
        cfg = _state["cfg"] or load_cfg()
        cfg.models = [m for m in cfg.models if m.id != model_id]
        save_cfg(cfg)
        _rebuild_registry(cfg)

    @app.get("/routing")
    async def get_routing() -> dict[str, Any]:
        cfg = _state["cfg"]
        pr = _state["prov_reg"]
        available = pr.available_model_ids() if pr else []
        if not cfg:
            return {"mode": "fixed", "default_model": None, "available_models": available}
        return {
            "mode": cfg.agent.routing_mode,
            "default_model": cfg.agent.default_model,
            "available_models": available,
        }

    @app.put("/routing")
    async def set_routing(body: dict[str, Any]) -> dict[str, Any]:
        from ..config_file import load as load_cfg, save as save_cfg
        cfg = _state["cfg"] or load_cfg()
        if "mode" in body:
            cfg.agent.routing_mode = body["mode"]
        if "default_model" in body:
            cfg.agent.default_model = body["default_model"]
        save_cfg(cfg)
        _rebuild_registry(cfg)
        return {"mode": cfg.agent.routing_mode, "default_model": cfg.agent.default_model}

    return app
