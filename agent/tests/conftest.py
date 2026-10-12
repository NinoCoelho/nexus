"""Test-suite global fixtures.

Auto-isolates the FastAPI lifespan from the user's real ``~/.nexus``
state so tests don't block on (or mutate) the user's local LLM setup.
"""
from __future__ import annotations

from collections.abc import Iterator

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
    # Keep the vault file-watcher (vault_watch.start_default) off in tests
    # that boot create_app against the real config/home — otherwise every
    # app test runs a watchdog observer on the developer's live vault.
    monkeypatch.setenv("NEXUS_DISABLE_VAULT_WATCH", "1")


@pytest.fixture(autouse=True)
def _reset_measured_overhead() -> Iterator[None]:
    """Isolate the process-global measured per-call overhead between tests.

    Any test that builds an Agent with a real tool registry triggers
    ``_builder._measure_call_overhead``, which sets the global for the
    whole process. Later tests asserting the 12K floor (usable_tokens,
    precheck budgets) then saw a stranger's measurement and failed only
    in full-suite runs. Same reset ``test_call_overhead`` does locally,
    applied suite-wide.
    """
    from nexus.agent.loop import overflow

    overflow.invalidate_measured_overhead()
    yield
    overflow.invalidate_measured_overhead()
