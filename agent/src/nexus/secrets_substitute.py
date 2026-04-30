"""Tool-boundary ``$NAME`` substitution.

The agent loop and skill bodies use ``$UPPERCASE_NAME`` placeholders for
credentials. Real values live in env vars or ``~/.nexus/secrets.toml``
and are looked up by :func:`nexus.secrets.resolve` (env first, store
fallback). The LLM never sees the resolved value — substitution is a
last-mile transform applied to the args of outbound tool calls
(``http_call``, ``acp_call``).

What is *not* substituted, by design:

* The ``terminal`` tool's ``command`` string. The user sees the command
  in the HITL approval dialog before it runs; baking a resolved secret
  into the displayed command would leak it. Skill authors who need a
  credential in a shell command should export the value as an
  environment variable so the spawned shell expands it itself — the
  pre-existing env-var path covers that case.
* Anything outside ``[A-Z][A-Z0-9_]*``. Lower-case shell variables and
  partial matches are left alone so ``"/path/$thing"`` keeps working.
"""

from __future__ import annotations

import re
from typing import Any

from . import secrets

_PLACEHOLDER_RE = re.compile(r"\$([A-Z][A-Z0-9_]*)\b")


def resolve(value: Any) -> Any:
    """Walk a JSON-like structure and substitute ``$NAME`` in every string.

    Unknown placeholders (no env var and no stored secret) are left
    untouched so a typo surfaces as a literal ``$NAME`` in the outgoing
    request rather than silently dropping the token.
    """
    if isinstance(value, str):
        return _resolve_str(value)
    if isinstance(value, list):
        return [resolve(v) for v in value]
    if isinstance(value, dict):
        return {k: resolve(v) for k, v in value.items()}
    return value


def _resolve_str(s: str) -> str:
    def _repl(m: re.Match[str]) -> str:
        name = m.group(1)
        val = secrets.resolve(name)
        return val if val is not None else m.group(0)

    return _PLACEHOLDER_RE.sub(_repl, s)
