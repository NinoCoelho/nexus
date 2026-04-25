"""Live integration tests against a local Ollama server running GLM-4.7-flash.

These tests are skipped automatically unless an Ollama server is reachable at
the URL pointed to by ``NEXUS_OLLAMA_URL`` (default ``http://localhost:11434``)
and the model named by ``NEXUS_OLLAMA_GLM_MODEL`` (default
``glm-4.7-flash:latest``) is present in ``/api/tags``.

Run them with::

    uv run pytest tests/test_ollama_glm_live.py -v -s

The goal is to exercise the full Nexus → OpenAIProvider → Ollama path the
same way the agent loop does, so we can spot capability gaps (reasoning
deltas being dropped, tool-calling behaviour, streaming finish frames, …).
"""

from __future__ import annotations

import os
import time

import httpx
import pytest
from loom.types import ToolSpec

from nexus.agent.llm.openai import OpenAIProvider
from nexus.agent.llm.types import ChatMessage, Role


OLLAMA_URL = os.environ.get("NEXUS_OLLAMA_URL", "http://localhost:11434").rstrip("/")
GLM_MODEL = os.environ.get("NEXUS_OLLAMA_GLM_MODEL", "glm-4.7-flash:latest")


def _ollama_has_model(url: str, model: str) -> bool:
    try:
        r = httpx.get(f"{url}/api/tags", timeout=3.0)
        if r.status_code != 200:
            return False
        names = {m.get("name") for m in (r.json().get("models") or [])}
        return model in names
    except httpx.HTTPError:
        return False


pytestmark = pytest.mark.skipif(
    not _ollama_has_model(OLLAMA_URL, GLM_MODEL),
    reason=f"Ollama at {OLLAMA_URL} does not expose model {GLM_MODEL!r}",
)


@pytest.fixture
async def provider() -> OpenAIProvider:
    p = OpenAIProvider(base_url=f"{OLLAMA_URL}/v1", api_key="ollama", model=GLM_MODEL)
    try:
        yield p
    finally:
        await p.aclose()


async def test_chat_non_stream_returns_content(provider: OpenAIProvider) -> None:
    """Baseline: the non-streaming path should return assistant content."""
    msgs = [ChatMessage(role=Role.USER, content="Reply with the single word OK.")]
    t0 = time.time()
    resp = await provider.chat(msgs)
    dur = time.time() - t0

    assert resp.content, f"empty content from non-stream call (took {dur:.1f}s)"
    assert "OK" in resp.content.upper()
    # GLM is a thinking model; it should still finish in well under the 120s
    # client timeout for a one-word answer.
    assert dur < 60, f"non-stream call too slow: {dur:.1f}s"


async def test_chat_stream_emits_content_or_tool(provider: OpenAIProvider) -> None:
    """The streaming path must end in a `finish` event with non-empty payload.

    Regression target: GLM-4.7-flash emits `delta.reasoning` chunks (the model's
    chain of thought) before / instead of `delta.content`. The OpenAIProvider
    currently only forwards `delta.content`, which means the agent sees zero
    progress events for many seconds while the model is "thinking". As long
    as the final `finish` event carries the assembled answer the loop still
    works — but no `delta` events ever fire, so the UI looks frozen.
    """
    msgs = [ChatMessage(role=Role.USER, content="Reply with the single word OK.")]
    deltas: list[str] = []
    finish_payload: dict | None = None

    t0 = time.time()
    async for ev in provider.chat_stream(msgs):
        if ev["type"] == "delta":
            deltas.append(ev["text"])
        elif ev["type"] == "finish":
            finish_payload = ev
    dur = time.time() - t0

    assert finish_payload is not None, "stream ended without a finish event"
    assert finish_payload["finish_reason"] in {"stop", "tool_use", "length"}
    assert finish_payload["content"], (
        f"finish payload had empty content (deltas={len(deltas)}, took {dur:.1f}s) — "
        "if this fires it means GLM emitted only `reasoning` chunks and our "
        "OpenAIProvider dropped them all on the floor"
    )


async def test_chat_stream_tool_call(provider: OpenAIProvider) -> None:
    """Tool calling over the OpenAI-compat endpoint should produce a structured
    tool_call in the finish frame (this is how the agent loop dispatches)."""
    tools = [
        ToolSpec(
            name="calc",
            description="Evaluate a small arithmetic expression",
            parameters={
                "type": "object",
                "properties": {"expr": {"type": "string"}},
                "required": ["expr"],
            },
        )
    ]
    msgs = [ChatMessage(role=Role.USER, content="What is 17*23? Use the calc tool.")]

    finish_payload: dict | None = None
    async for ev in provider.chat_stream(msgs, tools=tools):
        if ev["type"] == "finish":
            finish_payload = ev

    assert finish_payload is not None
    tcs = finish_payload.get("tool_calls") or []
    assert len(tcs) == 1, f"expected 1 tool call, got {tcs!r}"
    assert tcs[0]["name"] == "calc"
    assert "expr" in tcs[0]["arguments"]


async def test_chat_stream_emits_thinking_delta(provider: OpenAIProvider) -> None:
    """OpenAIProvider must surface GLM's chain-of-thought as `thinking_delta`
    events (separate from `delta`) so the agent loop can multiplex them."""
    msgs = [ChatMessage(role=Role.USER, content="Reply with the single word OK.")]
    n_thinking = 0
    n_delta = 0
    saw_finish = False
    async for ev in provider.chat_stream(msgs):
        if ev["type"] == "thinking_delta":
            n_thinking += 1
            assert isinstance(ev["text"], str) and ev["text"]
        elif ev["type"] == "delta":
            n_delta += 1
        elif ev["type"] == "finish":
            saw_finish = True
            # Finish content must remain CoT-free — reasoning is display only.
            assert "reasoning" not in (ev.get("content") or "").lower() or len(ev["content"]) < 100
    assert saw_finish
    assert n_thinking > 0, "expected GLM to emit reasoning chunks as thinking_delta"


async def test_reasoning_chunks_emitted_upstream() -> None:
    """Sanity check: GLM still emits `delta.reasoning` upstream (model contract)."""
    saw_reasoning = False
    saw_content = False
    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream(
            "POST",
            f"{OLLAMA_URL}/v1/chat/completions",
            json={
                "model": GLM_MODEL,
                "messages": [{"role": "user", "content": "Reply with the single word OK."}],
                "stream": True,
            },
        ) as resp:
            assert resp.status_code == 200
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if raw == "[DONE]":
                    break
                import json as _json
                try:
                    chunk = _json.loads(raw)
                except _json.JSONDecodeError:
                    continue
                delta = (chunk.get("choices") or [{}])[0].get("delta") or {}
                if delta.get("reasoning"):
                    saw_reasoning = True
                if delta.get("content"):
                    saw_content = True
                if saw_reasoning and saw_content:
                    break

    assert saw_reasoning, (
        "GLM-4.7-flash did not emit any `delta.reasoning` chunk — model behaviour changed?"
    )
    # Content should also eventually arrive; if this fails the model is
    # purely-reasoning, which would expose an even bigger gap.
    assert saw_content, "GLM emitted only reasoning, never content"


async def test_agent_run_turn_stream_yields_thinking_events() -> None:
    """End-to-end: Agent.run_turn_stream multiplexes thinking_delta events
    out of the LoomProviderAdapter sink and surfaces them as `thinking`."""
    from nexus.config_file import load as load_config, apply_env_overlay
    from nexus.agent.registry import build_registry
    from nexus.agent.loop import Agent
    from nexus.skills.registry import SkillRegistry
    from nexus.config import SKILLS_DIR

    cfg = apply_env_overlay(load_config())
    skill_reg = SkillRegistry(SKILLS_DIR)
    prov_reg = build_registry(cfg)
    try:
        default_provider, _ = prov_reg.get_for_model(f"ollama/{GLM_MODEL}")
    except KeyError:
        pytest.skip(f"ollama/{GLM_MODEL} not registered in ~/.nexus/config.toml")

    agent = Agent(
        provider=default_provider,
        registry=skill_reg,
        provider_registry=prov_reg,
        nexus_cfg=cfg,
    )
    try:
        n_thinking = 0
        n_delta = 0
        saw_done = False
        async for ev in agent.run_turn_stream(
            "Reply with the single word OK.",
            history=[],
            context=None,
            session_id="thinking-test",
            model_id=f"ollama/{GLM_MODEL}",
        ):
            t = ev.get("type")
            if t == "thinking":
                n_thinking += 1
            elif t == "delta":
                n_delta += 1
            elif t == "done":
                saw_done = True
                # The persisted assistant message must NOT contain the CoT —
                # reasoning is display-only, never written to history.
                msgs = ev.get("messages") or []
                assistant_msgs = [m for m in msgs if getattr(m, "role", None) == Role.ASSISTANT]
                assert assistant_msgs, "no assistant message in done.messages"
                last = assistant_msgs[-1].content or ""
                # Heuristic: GLM reasoning blocks always include "user" or
                # "request"; an OK reply is at most ~10 chars.
                assert len(last) < 200, f"assistant content unexpectedly long: {last!r}"
        assert saw_done
        assert n_thinking > 0, "Agent layer did not surface any `thinking` events"
    finally:
        await agent.aclose()
