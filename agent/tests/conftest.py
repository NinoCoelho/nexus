"""Test-suite global fixtures.

Auto-isolates the FastAPI lifespan from the user's real ``~/.nexus``
state so tests don't block on (or mutate) the user's local LLM setup.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _skip_local_llm_in_lifespan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Skip the lifespan's ``reap_orphans`` + ``restart_local_models``.

    Without this, every test that boots ``create_app`` walks the host
    process table killing ``llama-server`` instances and tries to spawn
    the user's GGUFs. Result: 5–10s of startup latency and accidental
    SIGTERM on the developer's running daemon.
    """
    monkeypatch.setenv("NEXUS_SKIP_LOCAL_LLM_RESTART", "1")
