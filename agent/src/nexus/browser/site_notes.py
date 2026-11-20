"""Site-notes distiller — knowledge, not recipes.

After a successful work episode on a site, a fast model distills the step
log into OPERATIONAL NOTES stored in the vault (``vault/sites/<host>.md``):
navigation paths, semantic anchors (visible text / roles — resilient to
redesigns, never raw CSS selectors), behavioral quirks, task patterns.

Reads on that host inject the note (excerpt + vault pointer), so future
sessions navigate with knowledge instead of re-discovering. Recorded flows
(saves/replay) remain as a disposable fast-path cache; when one breaks,
these notes are what let the agent repair cheaply.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from .extract import fast_call

log = logging.getLogger(__name__)

# Bounded: notes stay small enough to inject as an excerpt on every read.
NOTE_EXCERPT_CHARS = 1200
NOTE_MAX_CHARS = 6000
DISTILL_MIN_STEPS = 3
EPISODE_IDLE_SECONDS = 300.0


def sites_dir() -> Path:
    d = Path.home() / ".nexus" / "vault" / "sites"
    d.mkdir(parents=True, exist_ok=True)
    return d


def host_of(url: str) -> str:
    m = re.match(r"^[a-z]+://([^/:?#]+)", str(url or ""))
    host = (m.group(1) if m else str(url or "")).lower()
    host = host.removeprefix("www.")
    return re.sub(r"[^a-z0-9.-]+", "-", host)[:80] or "unknown"


def note_path(host: str) -> Path:
    return sites_dir() / f"{host_of(host)}.md"


def read_note(url_or_host: str) -> str | None:
    try:
        text = note_path(url_or_host).read_text()
    except OSError:
        return None
    return text if text.strip() else None


def note_excerpt(url_or_host: str) -> dict[str, str] | None:
    """The injectable hint: an excerpt + the vault pointer."""
    note = read_note(url_or_host)
    if not note:
        return None
    host = host_of(url_or_host)
    excerpt = note[:NOTE_EXCERPT_CHARS]
    if len(note) > NOTE_EXCERPT_CHARS:
        excerpt += "\n…[truncated — vault_read sites/" + host + ".md for the rest]"
    return {
        "excerpt": excerpt,
        "path": f"vault://sites/{host}.md",
    }


_SYSTEM = (
    "You maintain concise OPERATIONAL NOTES about a website for an AI agent "
    "that will navigate it again in future sessions. Update the existing "
    "notes with what these successful steps reveal.\n"
    "Rules:\n"
    "- Write ANCHORS as visible text, roles or aria-labels (resilient to "
    "redesign) — never raw CSS selectors (fragile, they rot)\n"
    "- Record: navigation paths, semantic anchors, page behaviors/quirks "
    "(virtualization, CSP, flyouts), task patterns that worked\n"
    "- Merge and dedupe with existing notes; drop stale contradictory info\n"
    "- Under 80 lines, plain markdown, sections: ## Navigation, ## Anchors, "
    "## Behaviors, ## Tasks\n"
    "- Write in the language of the existing notes (English if none)"
)


async def distill_and_save(
    host: str,
    steps: list[dict[str, Any]],
    url: str = "",
    title: str = "",
) -> Path | None:
    """Distill a successful step log into the site note (incremental merge)."""
    host = host_of(host)
    lines: list[str] = []
    for s in steps[-30:]:
        a = s.get("action")
        args = s.get("args") or {}
        bits = [str(a)]
        for k in ("url", "selector", "text", "ref"):
            if args.get(k) is not None:
                bits.append(f"{k}={args[k]}")
        if s.get("page_title"):
            bits.append(f"@ {s['page_title']}")
        lines.append("- " + " ".join(bits)[:200])
    if not lines:
        return None
    existing = read_note(host) or "(none yet)"
    prompt = (
        f"{_SYSTEM}\n\nSite: {host}\nLast page: {url or '(unknown)'} — {title or '(untitled)'}\n\n"
        f"Existing notes:\n{existing}\n\nSuccessful steps this session:\n"
        + "\n".join(lines)
        + "\n\nUpdated notes (markdown only, no commentary):"
    )
    try:
        note = await fast_call(prompt)
    except Exception as exc:  # noqa: BLE001 — distillation is best-effort
        log.warning("site-notes distill failed for %s: %s", host, exc)
        return None
    if not note or "```" in note[:20]:
        return None
    note = note.strip()
    if len(note) > NOTE_MAX_CHARS:
        note = note[:NOTE_MAX_CHARS] + "\n…[pruned]"
    path = note_path(host)
    path.write_text(note + "\n")
    return path
