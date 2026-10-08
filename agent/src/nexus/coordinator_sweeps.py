"""Sweep digest log for the coordinator.

Sweeps used to live in the master chat's persisted history, which made them
quadratically expensive: each sweep re-sent every previous sweep's prompt,
tool calls and tool results. Sweeps now run on a fresh context
(``run_background_turn(ephemeral=True)``) and their only durable record is
this append-only markdown log at ``~/.nexus/vault/.coordinator/sweeps.md``.

That buys three things for one small file:

* the user can read what the coordinator has been noticing, and what it cost;
* the next sweep gets the previous digest as its "what did I already report"
  seed, which is the one piece of continuity it actually needed from history;
* the file is vault-native, so it is searchable and the agent can read it with
  ``vault_read`` like any other note.

Everything here is best-effort — mirroring ``persist_session_summary``. A
sweep must never fail because its bookkeeping could not be written.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from .home import vault_coordinator

log = logging.getLogger(__name__)

_LOG_NAME = "sweeps.md"
# Entries are newest-first, so trimming keeps the head. 50 sweeps is ~4 days
# at the default 2-hour interval.
MAX_ENTRIES = 50
_HEADER = (
    "---\ntitle: Coordinator sweeps\n---\n\n"
    "Newest first. Written by the `coordinator_sweep` heartbeat driver; the\n"
    "most recent entry seeds the next sweep so it knows what it already\n"
    "reported.\n"
)
_ENTRY_RE = re.compile(r"^## \d{4}-\d{2}-\d{2}", re.M)


def _log_path():
    return vault_coordinator() / _LOG_NAME


def append_digest(
    digest: str,
    *,
    tokens_in: int = 0,
    tokens_out: int = 0,
    iterations: int = 0,
    model: str = "",
    when: datetime | None = None,
) -> str | None:
    """Prepend one timestamped digest entry. Returns the path, or None.

    The token counts ride along in the entry so "is the coordinator
    expensive?" is answerable by reading the file — sweeps are otherwise
    invisible to the heartbeat fire log, the job tracker and the UI.
    """
    body = (digest or "").strip()
    if not body:
        return None
    try:
        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = (when or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")
        meta: list[str] = []
        if tokens_in or tokens_out:
            meta.append(f"{tokens_in:,} in / {tokens_out:,} out")
        if iterations:
            meta.append(f"{iterations} iteration(s)")
        if model:
            meta.append(f"`{model}`")
        suffix = f"  \n_{' · '.join(meta)}_" if meta else ""
        entry = f"## {stamp}{suffix}\n\n{body}\n"

        existing = ""
        if path.is_file():
            existing = path.read_text(encoding="utf-8")
        entries_block = _strip_header(existing)
        combined = _trim(f"{entry}\n{entries_block}".rstrip() + "\n")
        path.write_text(f"{_HEADER}\n{combined}", encoding="utf-8")
        return str(path)
    except Exception:
        log.debug("coordinator sweeps: append failed", exc_info=True)
        return None


def load_last_digest() -> str | None:
    """Body of the newest entry, or None when there is no log yet."""
    try:
        path = _log_path()
        if not path.is_file():
            return None
        entries_block = _strip_header(path.read_text(encoding="utf-8"))
        matches = list(_ENTRY_RE.finditer(entries_block))
        if not matches:
            return None
        start = matches[0].start()
        end = matches[1].start() if len(matches) > 1 else len(entries_block)
        entry = entries_block[start:end].strip()
        # Drop the "## <stamp>" heading and the metadata line beneath it.
        lines = entry.splitlines()[1:]
        while lines and (not lines[0].strip() or lines[0].strip().startswith("_")):
            lines.pop(0)
        return "\n".join(lines).strip() or None
    except Exception:
        log.debug("coordinator sweeps: read failed", exc_info=True)
        return None


def _strip_header(text: str) -> str:
    """Return just the entry region, dropping any frontmatter + preamble."""
    if not text:
        return ""
    first = _ENTRY_RE.search(text)
    return text[first.start():] if first else ""


def _trim(entries_block: str, max_entries: int = MAX_ENTRIES) -> str:
    """Keep only the newest ``max_entries`` entries (the file is newest-first)."""
    matches = list(_ENTRY_RE.finditer(entries_block))
    if len(matches) <= max_entries:
        return entries_block
    cutoff = matches[max_entries].start()
    return entries_block[:cutoff].rstrip() + "\n"
