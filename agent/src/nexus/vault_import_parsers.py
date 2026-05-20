"""Parsers for ChatGPT / Claude / Gemini conversation exports.

Each parser reads the platform-specific JSON format and yields a list of
:class:`ExportConversation` objects.  The shared :func:`conversations_to_markdown`
then converts them to human-readable vault markdown files.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class ExportMessage:
    role: str
    content: str
    timestamp: str = ""


@dataclass
class ExportConversation:
    id: str
    title: str
    created_at: str
    messages: list[ExportMessage] = field(default_factory=list)
    model: str = ""
    source: str = ""


def detect_format(temp_dir: Path) -> str | None:
    conv_json = temp_dir / "conversations.json"
    if conv_json.is_file():
        try:
            with open(conv_json, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list) and data:
                first = data[0] if isinstance(data[0], dict) else {}
                if "mapping" in first:
                    return "chatgpt"
                if "chat_messages" in first or "uuid" in first:
                    return "claude"
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            pass
        return None

    gemini_path = temp_dir / "Gemini.json"
    if not gemini_path.is_file():
        gemini_path = temp_dir / "Takeout" / "Gemini" / "Gemini.json"
    if gemini_path.is_file():
        return "gemini"

    for p in temp_dir.rglob("*.json"):
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "chat_messages" in data:
                return "claude"
            if isinstance(data, list) and data and isinstance(data[0], dict) and "chat_messages" in data[0]:
                return "claude"
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            continue
    return None


def detect_format_from_filenames(filenames: list[str]) -> str | None:
    fn_set = {f.split("/")[-1] for f in filenames}
    if "conversations.json" in fn_set:
        return "chatgpt"
    if "Gemini.json" in fn_set:
        return "gemini"
    for f in filenames:
        if f.endswith(".json"):
            return "claude_maybe"
    return None


def _unix_to_iso(ts: float | int | str | None) -> str:
    if ts is None:
        return ""
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    except (ValueError, OSError, OverflowError):
        return ""


def _sanitize_filename(title: str, max_len: int = 80) -> str:
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title.strip())
    s = re.sub(r"_+", "_", s).strip("_ ")
    if not s:
        s = "untitled"
    if len(s) > max_len:
        s = s[:max_len].rstrip("_ ")
    return s


def _extract_text_from_parts(parts: list[Any]) -> str:
    chunks: list[str] = []
    for part in parts:
        if isinstance(part, str):
            chunks.append(part)
        elif isinstance(part, dict):
            ct = part.get("content_type", "text")
            if ct in ("text", "code", "multimodal_text", "thoughts", "reasoning_recap"):
                text = part.get("text", "")
                if text:
                    if ct == "code":
                        lang = part.get("language", "")
                        chunks.append(f"```{lang}\n{text}\n```")
                    else:
                        chunks.append(text)
            elif ct == "execution_output":
                text = part.get("text", "")
                if text:
                    chunks.append(f"```\n{text}\n```")
            elif ct in ("image_asset_pointer", "media"):
                desc = part.get("metadata", {}).get("dalle", {}).get("prompt", "")
                chunks.append(f"[Image{': ' + desc if desc else ''}]")
            elif ct in ("tether_browsing_display", "tether_quote", "citation"):
                text = part.get("text", "")
                if text:
                    chunks.append(text)
    return "\n\n".join(chunks)


def parse_chatgpt(conversations_json_path: Path) -> list[ExportConversation]:
    with open(conversations_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        data = [data]
    results: list[ExportConversation] = []
    for thread in data:
        if not isinstance(thread, dict):
            continue
        tid = thread.get("id", "")
        title = thread.get("title") or "Untitled"
        created_at = _unix_to_iso(thread.get("create_time"))
        default_model = thread.get("default_model_slug", "")
        mapping = thread.get("mapping", {})
        current_node = thread.get("current_node", "")
        chain: list[dict[str, Any]] = []
        visited: set[str] = set()
        nid = current_node
        while nid and nid not in visited:
            visited.add(nid)
            node = mapping.get(nid)
            if not isinstance(node, dict):
                break
            chain.append(node)
            pid = node.get("parent")
            nid = pid if pid else ""
        chain.reverse()
        messages: list[ExportMessage] = []
        for node in chain:
            msg = node.get("message")
            if not isinstance(msg, dict):
                continue
            author = msg.get("author", {})
            role = author.get("role", "")
            if role == "tool":
                continue
            if role == "system":
                continue
            mapped_role = "user" if role == "user" else "assistant"
            content = msg.get("content", {})
            parts = content.get("parts", []) if isinstance(content, dict) else []
            text = _extract_text_from_parts(parts) if parts else (str(content) if content else "")
            ts = _unix_to_iso(msg.get("create_time"))
            model_slug = msg.get("metadata", {}).get("model_slug", default_model)
            messages.append(ExportMessage(role=mapped_role, content=text, timestamp=ts))
            if model_slug:
                default_model = model_slug
        if messages:
            results.append(ExportConversation(
                id=tid, title=title, created_at=created_at,
                messages=messages, model=default_model, source="chatgpt",
            ))
    return results


def parse_claude(json_path: Path) -> list[ExportConversation]:
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = [data]
    results: list[ExportConversation] = []
    for conv in data:
        if not isinstance(conv, dict):
            continue
        cid = conv.get("uuid", conv.get("id", ""))
        title = conv.get("name") or conv.get("title") or ""
        if not title.strip():
            title = "Untitled"
        created_at = conv.get("created_at", "")
        raw_msgs = conv.get("chat_messages", conv.get("messages", []))
        if not raw_msgs:
            continue
        messages: list[ExportMessage] = []
        for m in raw_msgs:
            if not isinstance(m, dict):
                continue
            sender = m.get("sender", m.get("role", ""))
            role = "user" if sender in ("human", "user") else "assistant"
            text = ""
            raw_text = m.get("text", "")
            content_parts = m.get("content")
            if isinstance(content_parts, list) and content_parts:
                chunks: list[str] = []
                for part in content_parts:
                    if isinstance(part, dict):
                        ptype = part.get("type", "text")
                        ptext = part.get("text", "")
                        if ptype == "text" and ptext:
                            chunks.append(ptext)
                        elif ptype == "tool_use":
                            chunks.append(f"[Tool: {part.get('name', 'unknown')}]\n```json\n{json.dumps(part.get('input', {}), indent=2, ensure_ascii=False)}\n```")
                        elif ptype == "tool_result":
                            ctext = part.get("content", "")
                            if isinstance(ctext, str) and ctext:
                                chunks.append(ctext)
                            elif isinstance(ctext, list):
                                for cp in ctext:
                                    if isinstance(cp, dict) and cp.get("text"):
                                        chunks.append(cp["text"])
                        elif ptext:
                            chunks.append(ptext)
                    elif isinstance(part, str):
                        chunks.append(part)
                text = "\n\n".join(chunks)
            elif raw_text:
                text = raw_text
            if not text.strip():
                continue
            ts = m.get("created_at", "")
            messages.append(ExportMessage(role=role, content=text, timestamp=ts))
        if not messages:
            continue
        if title == "Untitled" and messages:
            first_text = messages[0].content[:80].replace("\n", " ").strip()
            title = first_text if first_text else "Untitled"
        results.append(ExportConversation(
            id=cid, title=title, created_at=created_at,
            messages=messages, source="claude",
        ))
    return results


def parse_gemini(gemini_json_path: Path) -> list[ExportConversation]:
    with open(gemini_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "messages" in data:
        data = [data]
    if not isinstance(data, list):
        data = [data]
    results: list[ExportConversation] = []
    for conv in data:
        if not isinstance(conv, dict):
            continue
        cid = conv.get("id", "")
        title = conv.get("title", "Untitled")
        created_at = conv.get("created_time", conv.get("created_at", ""))
        raw_msgs = conv.get("messages", [])
        messages: list[ExportMessage] = []
        for m in raw_msgs:
            if not isinstance(m, dict):
                continue
            role = "assistant" if m.get("role") == "model" else m.get("role", "user")
            text = m.get("content", m.get("text", ""))
            ts = m.get("timestamp", m.get("created_at", ""))
            messages.append(ExportMessage(role=role, content=text, timestamp=ts))
        if messages:
            results.append(ExportConversation(
                id=cid, title=title, created_at=created_at,
                messages=messages, source="gemini",
            ))
    return results


_SOURCE_LABELS = {"chatgpt": "ChatGPT", "claude": "Claude", "gemini": "Gemini"}


def conversations_to_markdown(
    convos: list[ExportConversation],
    dest_dir: str,
) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    seen_names: dict[str, int] = {}
    for conv in convos:
        base_name = _sanitize_filename(conv.title)
        if base_name in seen_names:
            seen_names[base_name] += 1
            base_name = f"{base_name}_{seen_names[base_name]}"
        else:
            seen_names[base_name] = 0
        filename = f"{base_name}.md"
        path = f"{dest_dir}/{filename}" if dest_dir else filename
        source_label = _SOURCE_LABELS.get(conv.source, conv.source)
        export_date = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        frontmatter_parts = [
            "---",
            f"source: {conv.source}",
            f"exported_date: \"{export_date}\"",
        ]
        if conv.model:
            frontmatter_parts.append(f"model: \"{conv.model}\"")
        frontmatter_parts.extend([
            f"message_count: {len(conv.messages)}",
            "---\n",
        ])
        body_parts = [f"# {conv.title}\n"]
        if conv.created_at:
            body_parts.append(f"> Imported from {source_label} · {conv.created_at}\n")
        else:
            body_parts.append(f"> Imported from {source_label}\n")
        for msg in conv.messages:
            role_label = "**You**" if msg.role == "user" else f"**{source_label}**"
            body_parts.append(f"\n### {role_label}\n")
            body_parts.append(msg.content)
            body_parts.append("")
        content = "\n".join(frontmatter_parts) + "\n".join(body_parts)
        results.append({"path": path, "content": content})
    return results
