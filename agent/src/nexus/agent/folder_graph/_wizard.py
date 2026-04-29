"""Multi-turn LLM-driven ontology proposer.

The wizard samples a few files from the folder, asks the configured chat
LLM to (a) propose entity types + relations and (b) emit a disambiguating
question whenever the corpus could plausibly fit two domains. Each user
answer feeds back to the LLM until the LLM emits ``next_question: null``
or the turn cap is reached.

Transport: the ``start_wizard`` async generator stays open as an SSE
stream and ``await``s on a per-session asyncio.Event between turns. The
``answer_wizard`` POST handler stores the answer and signals the event.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from ._scanner import iter_indexable_files
from ._storage import normalize_folder

log = logging.getLogger(__name__)

MAX_TURNS = 5
ANSWER_TIMEOUT_S = 60 * 30  # 30 min per answer; session evicted after that
SAMPLE_FILES = 8
SAMPLE_BYTES_PER_FILE = 2000

DEFAULT_ENTITY_TYPES = [
    "person", "organization", "concept", "document", "topic",
]
DEFAULT_RELATIONS = [
    "mentions", "about", "related_to", "part_of", "authored_by",
]


@dataclass
class WizardSession:
    wizard_id: str
    folder: str
    started_at: float = field(default_factory=time.time)
    turn: int = 0
    history: list[dict[str, str]] = field(default_factory=list)
    ontology: dict[str, Any] = field(default_factory=lambda: {
        "entity_types": list(DEFAULT_ENTITY_TYPES),
        "relations": list(DEFAULT_RELATIONS),
        "allow_custom_relations": True,
    })
    answer_event: asyncio.Event = field(default_factory=asyncio.Event)
    pending_answer: str | None = None
    finished: bool = False


_sessions: dict[str, WizardSession] = {}


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _sample_files(folder) -> list[tuple[str, str]]:
    files = list(iter_indexable_files(folder))
    if not files:
        return []
    chosen = random.sample(files, min(SAMPLE_FILES, len(files)))
    out: list[tuple[str, str]] = []
    for rel, abs_path, _, _ in chosen:
        try:
            text = abs_path.read_text(encoding="utf-8", errors="replace")[:SAMPLE_BYTES_PER_FILE]
        except OSError:
            continue
        out.append((rel, text))
    return out


_SYSTEM_PROMPT = """\
You are helping a non-technical user define a knowledge-graph ontology for a folder of text documents. \
You will see file samples (filename + first ~2000 chars). Propose:
  - 5-12 entity_types (singular, lowercase, snake_case),
  - 5-12 relations (verb phrases, snake_case),
  - allow_custom_relations: true/false (true unless the corpus is highly structured).

If the corpus is genuinely ambiguous (e.g., could be medical records OR prescription guides),
ask ONE clarifying question with 2-4 options. Otherwise leave next_question null and emit your final ontology.

ALWAYS respond with a single JSON object, no prose, no markdown fences:
{
  "ontology": {"entity_types": [...], "relations": [...], "allow_custom_relations": bool},
  "next_question": {"text": "...", "choices": ["...", "..."]} | null,
  "rationale": "1-2 sentence summary of what this folder appears to be about"
}
"""


def _user_prompt(samples: list[tuple[str, str]], history: list[dict[str, str]]) -> str:
    parts = ["File samples:\n"]
    for rel, text in samples:
        parts.append(f"--- {rel} ---\n{text}\n")
    if history:
        parts.append("\nPrior clarifications:")
        for entry in history:
            parts.append(f"Q: {entry['question']}\nA: {entry['answer']}")
    parts.append("\nReturn the JSON described in the system prompt.")
    return "\n".join(parts)


def _resolve_chat_llm(cfg: Any):
    """Return ``(NexusLLMProvider, upstream_name)`` for the wizard.

    Prefers ``cfg.agent.default_model`` (the user's main chat model) over
    extraction-only models since the wizard is a conversational task.
    Returns ``(None, None)`` if nothing is configured.
    """
    from ..registry import build_registry

    model_id = (
        getattr(getattr(cfg, "agent", None), "default_model", "")
        or getattr(getattr(cfg, "graphrag", None), "extraction_model_id", "")
        or ""
    )
    if not model_id:
        return None, None
    try:
        registry = build_registry(cfg)
        provider, upstream = registry.get_for_model(model_id)
        return provider, upstream
    except Exception:
        log.warning("[folder_graph] could not resolve chat model %r for wizard", model_id,
                    exc_info=True)
        return None, None


async def _ask_llm(cfg: Any, samples: list[tuple[str, str]],
                   history: list[dict[str, str]]) -> dict[str, Any] | None:
    """Single LLM call. Returns parsed JSON or None on failure."""
    from ..llm import ChatMessage, Role

    provider, upstream = _resolve_chat_llm(cfg)
    if provider is None:
        return None
    messages = [
        ChatMessage(role=Role.SYSTEM, content=_SYSTEM_PROMPT),
        ChatMessage(role=Role.USER, content=_user_prompt(samples, history)),
    ]
    try:
        resp = await provider.chat(messages, tools=[], model=upstream)
        raw = (resp.content or "").strip()
        # Strip ``` fences if the model emits them despite instructions.
        if raw.startswith("```"):
            raw = raw.strip("`")
            # remove leading "json\n" if present
            if raw.startswith("json"):
                raw = raw[4:].lstrip()
        return json.loads(raw)
    except Exception:
        log.warning("[folder_graph] wizard LLM call failed", exc_info=True)
        return None


def _coerce_ontology(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {"entity_types": list(DEFAULT_ENTITY_TYPES),
                "relations": list(DEFAULT_RELATIONS),
                "allow_custom_relations": True}
    et = raw.get("entity_types")
    rel = raw.get("relations")
    return {
        "entity_types": [str(x).strip() for x in et if str(x).strip()] if isinstance(et, list) else list(DEFAULT_ENTITY_TYPES),
        "relations": [str(x).strip() for x in rel if str(x).strip()] if isinstance(rel, list) else list(DEFAULT_RELATIONS),
        "allow_custom_relations": bool(raw.get("allow_custom_relations", True)),
    }


def _coerce_question(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    text = str(raw.get("text") or "").strip()
    if not text:
        return None
    choices = raw.get("choices")
    if not isinstance(choices, list):
        choices = []
    return {
        "text": text,
        "choices": [str(c).strip() for c in choices if str(c).strip()][:4],
    }


def get_session(wizard_id: str) -> WizardSession | None:
    return _sessions.get(wizard_id)


def _evict_stale_sessions() -> None:
    cutoff = time.time() - ANSWER_TIMEOUT_S
    for wid, session in list(_sessions.items()):
        if session.started_at < cutoff:
            _sessions.pop(wid, None)


async def start_wizard(folder: str, cfg: Any) -> AsyncIterator[str]:
    """SSE generator. First event is `wizard_id`; thereafter `proposal`,
    `question`, `done`, or `error`. Closes the session when finished/erroring.
    """
    _evict_stale_sessions()
    folder_p = normalize_folder(folder)
    if not folder_p.is_dir():
        yield _sse("error", {"detail": f"folder does not exist: {folder_p}"})
        return

    samples = _sample_files(folder_p)
    if not samples:
        yield _sse("error", {"detail": "no .md/.txt files found to sample"})
        return

    session = WizardSession(wizard_id=str(uuid.uuid4()), folder=str(folder_p))
    _sessions[session.wizard_id] = session
    yield _sse("wizard_id", {"wizard_id": session.wizard_id})

    try:
        while session.turn < MAX_TURNS and not session.finished:
            session.turn += 1
            yield _sse("status", {"message": f"Asking model (turn {session.turn}/{MAX_TURNS})…"})
            data = await _ask_llm(cfg, samples, session.history)
            if data is None:
                yield _sse("error", {
                    "detail": "wizard LLM unavailable — check that a chat model is configured",
                })
                return

            ontology = _coerce_ontology(data.get("ontology"))
            session.ontology = ontology
            rationale = str(data.get("rationale") or "")
            yield _sse("proposal", {
                "ontology": ontology,
                "rationale": rationale,
                "turn": session.turn,
            })

            question = _coerce_question(data.get("next_question"))
            if question is None:
                session.finished = True
                yield _sse("done", {"ontology": ontology, "rationale": rationale})
                return

            # Reset the event BEFORE yielding the question so an answer that
            # arrives between the yield and the resume isn't clobbered.
            session.answer_event.clear()
            yield _sse("question", {"question": question, "turn": session.turn})

            try:
                await asyncio.wait_for(session.answer_event.wait(), timeout=ANSWER_TIMEOUT_S)
            except asyncio.TimeoutError:
                yield _sse("error", {"detail": "wizard timed out waiting for answer"})
                return

            answer = session.pending_answer or ""
            session.pending_answer = None
            session.history.append({"question": question["text"], "answer": answer})

        # Hit MAX_TURNS without the LLM signalling done — finalize what we have.
        if not session.finished:
            yield _sse("done", {
                "ontology": session.ontology,
                "rationale": "Reached max turns; finalizing collected proposal.",
            })
    finally:
        _sessions.pop(session.wizard_id, None)


def answer_wizard(wizard_id: str, answer: str) -> bool:
    """Push the user's answer to a running wizard. True if accepted."""
    session = _sessions.get(wizard_id)
    if session is None or session.finished:
        return False
    session.pending_answer = answer or ""
    session.answer_event.set()
    return True
