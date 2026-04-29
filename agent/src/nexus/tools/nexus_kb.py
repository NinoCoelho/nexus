"""nexus_kb_search — BM25 retrieval over the bundled `nexus` skill's
``knowledge.md``. Lets the agent answer "how do I X in Nexus?" questions
without paying for the full knowledge base in the system prompt.

The index is built lazily on first call and cached for the process
lifetime. Restart picks up edits to ``knowledge.md``.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from threading import Lock

from ..agent.llm import ToolSpec

log = logging.getLogger(__name__)

KB_PATH = Path.home() / ".nexus" / "skills" / "nexus" / "knowledge.md"

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _split_sections(text: str) -> list[tuple[str, str]]:
    """Split a markdown doc on ``## `` headers. Returns (header, body) pairs.

    Content above the first ``## `` is dropped (treated as a preamble).
    Headers deeper than ``## `` stay inside their parent section.
    """
    sections: list[tuple[str, str]] = []
    current_header: str | None = None
    current_body: list[str] = []
    for line in text.splitlines():
        if line.startswith("## ") and not line.startswith("### "):
            if current_header is not None:
                sections.append((current_header, "\n".join(current_body).strip()))
            current_header = line[3:].strip()
            current_body = []
        else:
            if current_header is not None:
                current_body.append(line)
    if current_header is not None:
        sections.append((current_header, "\n".join(current_body).strip()))
    return [(h, b) for h, b in sections if b]


class _KBIndex:
    """Process-lifetime BM25 index over knowledge.md. Built on first query."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._built = False
        self._headers: list[str] = []
        self._bodies: list[str] = []
        self._bm25 = None  # type: ignore[assignment]
        self._error: str | None = None

    def _build(self) -> None:
        if self._built:
            return
        if not KB_PATH.is_file():
            self._error = f"knowledge file not found at {KB_PATH}"
            self._built = True
            return
        try:
            from rank_bm25 import BM25Okapi  # type: ignore
        except ImportError as e:
            self._error = f"rank_bm25 not installed: {e}"
            self._built = True
            return
        try:
            text = KB_PATH.read_text(encoding="utf-8")
        except OSError as e:
            self._error = f"could not read knowledge file: {e}"
            self._built = True
            return
        sections = _split_sections(text)
        if not sections:
            self._error = "knowledge file has no ## sections"
            self._built = True
            return
        self._headers = [h for h, _ in sections]
        self._bodies = [b for _, b in sections]
        # Index header + body together so a query matching the section title
        # alone still ranks the section highly.
        corpus = [_tokenize(f"{h}\n{b}") for h, b in sections]
        self._bm25 = BM25Okapi(corpus)
        self._built = True
        log.info("[nexus_kb] indexed %d sections from %s", len(sections), KB_PATH)

    def search(self, query: str, k: int) -> list[dict]:
        with self._lock:
            self._build()
        if self._error:
            return [{"error": self._error}]
        if not query.strip():
            return []
        tokens = _tokenize(query)
        if not tokens:
            return []
        scores = self._bm25.get_scores(tokens)  # type: ignore[union-attr]
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        results: list[dict] = []
        for i in order[: max(1, k)]:
            if scores[i] <= 0:
                continue
            results.append(
                {
                    "header": self._headers[i],
                    "body": self._bodies[i],
                    "score": round(float(scores[i]), 3),
                }
            )
        return results


_INDEX = _KBIndex()


NEXUS_KB_TOOL = ToolSpec(
    name="nexus_kb_search",
    description=(
        "Search the bundled Nexus knowledge base for an answer to a "
        "'how do I X in Nexus?' question. Returns the top-k sections of "
        "~/.nexus/skills/nexus/knowledge.md ranked by BM25 over the user's "
        "query. Each result is a self-contained section with its header. "
        "Call this from the `nexus` skill before answering meta-questions "
        "about Nexus configuration, settings, or features."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "A short keyword query distilled from the user's question "
                    "(e.g. 'configure model openai', 'change theme color')."
                ),
            },
            "k": {
                "type": "integer",
                "description": "Number of top sections to return (default 3, max 10).",
                "default": 3,
            },
        },
        "required": ["query"],
    },
)


def handle_nexus_kb_search(args: dict) -> dict:
    query = str(args.get("query", ""))
    k_raw = args.get("k", 3)
    try:
        k = int(k_raw)
    except (TypeError, ValueError):
        k = 3
    k = max(1, min(10, k))
    results = _INDEX.search(query, k)
    return {"query": query, "results": results}
