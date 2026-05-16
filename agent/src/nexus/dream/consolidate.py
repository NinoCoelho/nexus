"""Memory consolidation phase — deduplicates, fixes dates, prunes stale entries."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_CONSOLIDATION_SYSTEM_PROMPT = """\
You are a memory consolidation engine. You receive the contents of memory notes
from a personal AI agent and produce a structured merge plan as JSON.

Your job:
1. Find duplicate entries about the same topic and propose merges.
2. Find contradictions (e.g. "project uses Express" vs "project uses Fastify")
   and resolve with recency bias.
3. Convert relative dates ("yesterday", "last week", "recently") to absolute
   dates using today's date: {today}.
4. Find references to vault files that likely no longer exist (generic refs
   without specific paths) and flag them.
5. Remove entries that are clearly stale (e.g. "currently working on X" for
   something completed months ago).

Output ONLY valid JSON matching this schema:
{{
  "actions": [
    {{"op": "merge", "sources": ["path/a.md", "path/b.md"], "target": "path/a.md", "merged_content": "...", "reason": "..."}},
    {{"op": "update", "path": "path/c.md", "updated_content": "...", "reason": "..."}},
    {{"op": "delete", "path": "path/d.md", "reason": "..."}},
    {{"op": "flag", "path": "path/e.md", "issue": "..."}}
  ]
}}

Rules:
- Only propose changes you are confident about. When in doubt, use "flag".
- merged_content / updated_content must be complete markdown (not partial).
- Do NOT propose changes to files not listed in the input.
- Max 10 actions per consolidation pass.
- If nothing needs changing, return {{"actions": []}}.
"""


@dataclass
class ConsolidationResult:
    actions_applied: int = 0
    merges: int = 0
    updates: int = 0
    deletes: int = 0
    flags: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0


async def run_consolidation(
    *,
    provider: Any,
    model_id: str | None = None,
    max_tokens: int = 4000,
    context_budget: int = 8000,
    vault_memory_dir: Path | None = None,
) -> ConsolidationResult:
    if vault_memory_dir is None:
        vault_memory_dir = Path.home() / ".nexus" / "vault" / "memory"

    memory_files = _load_memory_files(vault_memory_dir)
    if not memory_files:
        log.info("dream/consolidate: no memory files found, skipping")
        return ConsolidationResult()

    chunks = _chunk_files(memory_files, context_budget)
    result = ConsolidationResult()

    for chunk in chunks:
        chunk_result = await _consolidate_chunk(
            chunk, provider=provider, model_id=model_id,
            max_tokens=max_tokens,
        )
        result.merges += chunk_result.merges
        result.updates += chunk_result.updates
        result.deletes += chunk_result.deletes
        result.flags.extend(chunk_result.flags)
        result.errors.extend(chunk_result.errors)
        result.actions_applied += chunk_result.actions_applied
        result.tokens_in += chunk_result.tokens_in
        result.tokens_out += chunk_result.tokens_out

    return result


def _load_memory_files(memory_dir: Path) -> list[dict[str, str]]:
    files: list[dict[str, str]] = []
    if not memory_dir.exists():
        return files

    for md_file in sorted(memory_dir.rglob("*.md")):
        try:
            content = md_file.read_text(encoding="utf-8")
            rel = str(md_file.relative_to(memory_dir))
            files.append({"path": rel, "content": content})
        except Exception:
            log.warning("dream/consolidate: failed to read %s", md_file, exc_info=True)
    return files


def _chunk_files(
    files: list[dict[str, str]], budget: int,
) -> list[list[dict[str, str]]]:
    chunks: list[list[dict[str, str]]] = []
    current: list[dict[str, str]] = []
    current_size = 0

    for f in files:
        size = len(f["content"])
        if current and current_size + size > budget:
            chunks.append(current)
            current = []
            current_size = 0
        current.append(f)
        current_size += size

    if current:
        chunks.append(current)
    return chunks


async def _consolidate_chunk(
    files: list[dict[str, str]],
    *,
    provider: Any,
    model_id: str | None,
    max_tokens: int,
) -> ConsolidationResult:
    from ..agent.llm import ChatMessage as LLMChatMessage, Role

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    system = _CONSOLIDATION_SYSTEM_PROMPT.format(today=today)

    file_list = "\n\n".join(
        f"--- FILE: {f['path']} ---\n{f['content']}" for f in files
    )

    messages = [
        LLMChatMessage(role=Role.SYSTEM, content=system),
        LLMChatMessage(role=Role.USER, content=file_list),
    ]

    try:
        response = await provider.chat(
            messages, model=model_id, max_tokens=max_tokens,
        )
    except Exception:
        log.exception("dream/consolidate: LLM call failed")
        return ConsolidationResult(errors=["LLM call failed"])

    raw = response.content.strip()
    tokens_in = response.usage.input_tokens
    tokens_out = response.usage.output_tokens
    parsed = _extract_json(raw)
    if parsed is None:
        import hashlib
        import tempfile
        h = hashlib.md5(raw.encode()).hexdigest()[:8]
        dump = Path(tempfile.gettempdir()) / f"dream_parse_fail_{h}.json"
        dump.write_text(raw, encoding="utf-8")
        log.warning(
            "dream/consolidate: failed to parse LLM output as JSON (len=%d, dumped=%s)",
            len(raw), dump,
        )
        return ConsolidationResult(errors=["Failed to parse merge plan"], tokens_in=tokens_in, tokens_out=tokens_out)

    actions = parsed.get("actions", [])
    if not isinstance(actions, list):
        return ConsolidationResult(errors=["Merge plan 'actions' is not a list"], tokens_in=tokens_in, tokens_out=tokens_out)

    result = _execute_actions(actions, files)
    result.tokens_in = tokens_in
    result.tokens_out = tokens_out
    return result


def _extract_json(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    candidate = text.strip()
    if candidate.startswith("```"):
        first_nl = candidate.find("\n")
        if first_nl >= 0:
            candidate = candidate[first_nl + 1:]
        last_fence = candidate.rfind("```")
        if last_fence > 0:
            candidate = candidate[:last_fence]
    candidate = candidate.strip()
    if not candidate:
        return None
    brace = candidate.find("{")
    bracket = candidate.find("[")
    if brace >= 0 and (bracket < 0 or brace <= bracket):
        candidate = candidate[brace:]
    elif bracket >= 0:
        candidate = candidate[bracket:]
    try:
        result = json.loads(candidate)
        if isinstance(result, dict):
            return result
        if isinstance(result, list) and len(result) > 0 and isinstance(result[0], dict):
            return result[0]
    except json.JSONDecodeError:
        pass
    try:
        brace = candidate.rfind("}")
        if brace >= 0:
            result = json.loads(candidate[: brace + 1])
            if isinstance(result, dict):
                return result
    except json.JSONDecodeError:
        pass
    return None


def _execute_actions(
    actions: list[dict[str, Any]],
    source_files: list[dict[str, str]],
) -> ConsolidationResult:
    from .. import vault

    result = ConsolidationResult()
    source_paths = {f["path"] for f in source_files}

    for action in actions[:10]:
        op = action.get("op", "")
        path = action.get("path", "")

        if op == "flag":
            result.flags.append(f"{path}: {action.get('issue', 'unknown')}")
            continue

        if op == "merge":
            sources = action.get("sources", [])
            target = action.get("target", "")
            merged = action.get("merged_content", "")
            if not all(s in source_paths for s in sources):
                result.errors.append("merge: source path out of scope")
                continue
            if target not in source_paths:
                result.errors.append("merge: target path out of scope")
                continue
            try:
                vault.write_file(f"memory/{target}", merged)
                for src in sources:
                    if src != target:
                        try:
                            vault.delete(f"memory/{src}")
                        except FileNotFoundError:
                            pass
                result.merges += 1
                result.actions_applied += 1
            except Exception:
                result.errors.append(f"merge: failed to write {target}")
                log.exception("dream/consolidate: merge write failed for %s", target)
            continue

        if op == "update":
            updated = action.get("updated_content", "")
            if path not in source_paths:
                result.errors.append("update: path out of scope")
                continue
            try:
                vault.write_file(f"memory/{path}", updated)
                result.updates += 1
                result.actions_applied += 1
            except Exception:
                result.errors.append(f"update: failed to write {path}")
                log.exception("dream/consolidate: update write failed for %s", path)
            continue

        if op == "delete":
            if path not in source_paths:
                result.errors.append("delete: path out of scope")
                continue
            try:
                vault.delete(f"memory/{path}")
                result.deletes += 1
                result.actions_applied += 1
            except FileNotFoundError:
                pass
            except Exception:
                result.errors.append(f"delete: failed for {path}")
                log.exception("dream/consolidate: delete failed for %s", path)
            continue

    return result
