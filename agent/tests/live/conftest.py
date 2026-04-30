"""Shared fixtures + skip helpers for live provider tests.

Each live test file checks for the credentials it needs and skips
cleanly when they're absent — so a normal ``pytest`` run never tries
to spend money. Run them explicitly with::

    NEXUS_LIVE_TESTS=1 uv run pytest tests/live/

(or any of the per-provider env vars below.)

Cost guardrails:
- All tests cap ``max_tokens`` low (≤300) and use the cheapest model
  for the scenario.
- Tool-calling tests use a no-op echo tool so we don't drift into
  expensive multi-turn loops.
- Reasoning tests use mini variants where available.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# Master switch. Must be set OR a per-provider env var must be present.
_MASTER = "NEXUS_LIVE_TESTS"


def env_or_skip(*names: str) -> str:
    """Return the first env var that's set, or skip the test."""
    for n in names:
        v = os.environ.get(n, "").strip()
        if v:
            return v
    pytest.skip(f"none of {names} set — skipping live test")


def skip_unless_live() -> None:
    """Hard skip if the live-test umbrella switch is off AND no
    provider-specific env var is set. Use at module-collection time."""
    if os.environ.get(_MASTER, "").strip():
        return
    # If any per-provider key is set we still allow the test to run.
    if any(
        os.environ.get(k, "").strip()
        for k in (
            "NEXUS_LIVE_ANTHROPIC_KEY",
            "NEXUS_LIVE_OPENAI_KEY",
            "NEXUS_LIVE_BEDROCK_PROFILE",
        )
    ):
        return
    # Auto-detect the local Claude Code / Codex cred files; these are
    # cheap to call (using the user's existing subscription).
    if Path("~/.codex/auth.json").expanduser().exists():
        return
    pytest.skip(
        f"Live tests off. Set {_MASTER}=1 (or a provider-specific env var) to enable.",
        allow_module_level=True,
    )


def skip_unless_macos_keychain_has_claude_code() -> None:
    """Skip when the Claude Code keychain entry isn't readable."""
    import platform
    import subprocess

    if platform.system() != "Darwin":
        pytest.skip("Claude Code OAuth lift is macOS-only in this build")
    try:
        proc = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pytest.skip("`security` CLI not available")
    if proc.returncode != 0:
        pytest.skip("Claude Code keychain entry not present — sign in to claude-code first")


# A no-op echo tool used by tool-calling tests. The model is asked to
# call it once with a specific argument; we only verify the SHAPE of
# the call (id, name, arguments dict), not what the model returns next.
def echo_tool_spec():
    from loom.types import ToolSpec

    return ToolSpec(
        name="echo",
        description="Echoes the provided text back. Call this tool exactly once.",
        parameters={
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Text to echo. Use the literal phrase 'hello-from-test'.",
                },
            },
            "required": ["text"],
        },
    )
