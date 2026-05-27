"""Tool-boundary ``$NAME`` substitution.

The agent loop and skill bodies use ``$UPPERCASE_NAME`` placeholders for
credentials. Real values live in env vars or ``~/.nexus/secrets.toml``
and are looked up by :func:`nexus.secrets.resolve` (env first, store
fallback). The LLM never sees the resolved value — substitution is a
last-mile transform applied to the args of outbound tool calls
(``http_call``, ``acp_call``).

For ``terminal`` commands, ``$NAME`` placeholders are NOT substituted in
the command string (to avoid leaking secrets in the HITL approval
dialog). Instead, the ``_terminal`` wrapper in ``registry.py`` scans
the command for ``$NAME`` patterns, resolves them via
:func:`nexus.secrets.resolve`, and injects the values as environment
variables into the subprocess. The shell then expands ``$NAME``
naturally — the command string itself is never modified.

What is *not* substituted, by design:

* The ``terminal`` tool's ``command`` string (string substitution).
  Credential values are instead injected as subprocess env vars so the
  shell expands them. The command shown in the HITL approval dialog
  always contains the literal ``$NAME`` placeholder, never the value.
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
