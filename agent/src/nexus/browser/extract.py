"""Sub-LLM page extraction — the browser-use ``extract`` trick.

The page text never enters the agent's context: the tool reads the page
with the normal probe caps, hands it to a fast model, and returns ONLY the
answer. This is the lever against per-turn tool-budget blowouts — a page
worth ~2-3k tokens of read becomes a ~50-token answer.

The LLM call is a late-bound callable (the app wires it where an Agent and
its provider registry exist — same pattern as the delegate/panel handlers).
When unwired, both tools degrade with a clear error instead of guessing.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

log = logging.getLogger(__name__)

# async fn(prompt: str) -> str
_llm_call: Callable[[str], Awaitable[str]] | None = None


def set_extract_llm(fn: Callable[[str], Awaitable[str]] | None) -> None:
    global _llm_call
    _llm_call = fn


def extract_available() -> bool:
    return _llm_call is not None


async def fast_call(prompt: str) -> str:
    """One-shot call on the late-bound fast model (compact_model fallback:
    default). Shared by extract and the site-notes distiller."""
    if _llm_call is None:
        raise RuntimeError("fast LLM not wired (server setup incomplete)")
    return (await _llm_call(prompt)).strip()


async def extract_answer(
    question: str,
    page_text: str,
    *,
    url: str = "",
    title: str = "",
) -> str:
    """Answer ``question`` using only ``page_text`` (fresh page read)."""
    if _llm_call is None:
        raise RuntimeError(
            "extract LLM not wired (server setup incomplete) — use read instead"
        )
    q = question.strip()
    if not q:
        raise RuntimeError("extract requires a question")
    prompt = (
        "You answer questions about a web page. Use ONLY the page content "
        "below — never outside knowledge. Be concise and direct; if the page "
        "does not answer the question, say exactly that.\n\n"
        f"Question: {q}\n\n"
        f"Page: {url or '(unknown url)'} — {title or '(untitled)'}\n\n"
        f"<page>\n{page_text}\n</page>"
    )
    return (await _llm_call(prompt)).strip()
