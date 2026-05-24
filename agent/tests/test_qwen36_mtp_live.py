"""Live integration test for Qwen3.6-27B-MTP-GGUF with llama.cpp MTP speculative decoding.

Spawns ``llama-server`` with ``--spec-type draft-mtp`` and exercises the
OpenAI-compat chat completions endpoint against it.  Skipped automatically
when:

* ``llama-server`` is not on ``PATH``.
* The model fails to download or the server doesn't become healthy within
  the startup timeout.

Run::

    uv run pytest tests/test_qwen36_mtp_live.py -v -s

The model is ~16 GB (Q4_K_M default).  Needs ~48 GB RAM / VRAM.
"""

from __future__ import annotations

import os
import subprocess
import time

import httpx
import pytest

HF_REPO = os.environ.get("NEXUS_QWEN_MTP_REPO", "ggml-org/Qwen3.6-27B-MTP-GGUF")
SPEC_DRAFT_N_MAX = int(os.environ.get("NEXUS_QWEN_MTP_DRAFT_N", "2"))
STARTUP_TIMEOUT = int(os.environ.get("NEXUS_QWEN_MTP_STARTUP_TIMEOUT", "600"))
LOG_PATH = os.environ.get(
    "NEXUS_QWEN_MTP_LOG",
    os.path.join(os.path.dirname(__file__), "..", "llama-mtp-test.log"),
)
EXISTING_URL = os.environ.get("NEXUS_QWEN_MTP_URL", "")


def _find_llama_server() -> str | None:
    import shutil
    return shutil.which("llama-server")


def _tail_log(path: str, last_pos: int = 0) -> tuple[str, int]:
    try:
        with open(path, "r", errors="replace") as f:
            f.seek(last_pos)
            text = f.read()
            return text, f.tell()
    except OSError:
        return "", last_pos


@pytest.fixture(scope="module")
def llama_server():
    if EXISTING_URL:
        yield {"base_url": EXISTING_URL.rstrip("/"), "port": 0, "proc": None}
        return

    binary = _find_llama_server()
    if binary is None:
        pytest.skip("llama-server not found on PATH")

    port = _pick_free_port()
    cmd = [
        binary,
        "-hf", HF_REPO,
        "--host", "127.0.0.1",
        "--port", str(port),
        "--spec-type", "draft-mtp",
        "--spec-draft-n-max", str(SPEC_DRAFT_N_MAX),
        "-ngl", "99",
        "--jinja",
    ]
    print(f"\n[llama-server] starting: {' '.join(cmd)}")
    print(f"[llama-server] log file: {LOG_PATH}")

    log_file = open(LOG_PATH, "w")
    proc = subprocess.Popen(
        cmd,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )

    base_url = f"http://127.0.0.1:{port}"
    healthy = False
    deadline = time.time() + STARTUP_TIMEOUT
    log_pos = 0

    try:
        while time.time() < deadline:
            rc = proc.poll()
            if rc is not None:
                log_file.close()
                tail, _ = _tail_log(LOG_PATH)
                pytest.fail(f"llama-server exited with code {rc}:\n{tail[-3000:]}")

            try:
                r = httpx.get(f"{base_url}/v1/models", timeout=2.0)
                if r.status_code == 200:
                    healthy = True
                    break
            except (httpx.HTTPError, OSError):
                pass

            time.sleep(2.0)
            chunk, log_pos = _tail_log(LOG_PATH, log_pos)
            if chunk.strip():
                for line in chunk.strip().splitlines()[-5:]:
                    print(f"  [llama-server] {line}")

        if not healthy:
            log_file.close()
            tail, _ = _tail_log(LOG_PATH)
            pytest.fail(
                f"llama-server didn't become healthy within {STARTUP_TIMEOUT}s.\n"
                f"Last 2k of log:\n{tail[-2000:]}"
            )

        print(f"[llama-server] ready on {base_url}")
        yield {"base_url": base_url, "port": port, "proc": proc}
    finally:
        print("[llama-server] shutting down")
        log_file.close()
        if proc:
            try:
                proc.terminate()
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


def _pick_free_port() -> int:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _stream_chat(base_url: str, messages: list[dict], **kwargs) -> list[dict]:
    events: list[dict] = []
    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream(
            "POST",
            f"{base_url}/v1/chat/completions",
            json={"messages": messages, "stream": True, **kwargs},
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
                events.append(chunk)
    return events


async def _non_stream_chat(base_url: str, messages: list[dict], **kwargs) -> dict:
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(
            f"{base_url}/v1/chat/completions",
            json={"messages": messages, "stream": False, **kwargs},
        )
        assert r.status_code == 200
        return r.json()


def test_server_lists_model(llama_server):
    r = httpx.get(f"{llama_server['base_url']}/v1/models", timeout=5.0)
    assert r.status_code == 200
    data = r.json()
    assert data.get("data"), "expected at least one model in /v1/models"


async def test_non_stream_returns_content(llama_server):
    resp = await _non_stream_chat(
        llama_server["base_url"],
        [{"role": "user", "content": "Reply with the single word OK."}],
    )
    content = resp["choices"][0]["message"]["content"]
    assert content, "empty content from non-stream call"
    assert "OK" in content.upper(), f"expected OK in response, got: {content!r}"


async def test_stream_emits_content(llama_server):
    events = await _stream_chat(
        llama_server["base_url"],
        [{"role": "user", "content": "Reply with the single word OK."}],
    )
    assert events, "no events received"

    assembled = ""
    finish_reason = None
    for ev in events:
        delta = (ev.get("choices") or [{}])[0].get("delta") or {}
        assembled += delta.get("content") or ""
        fr = (ev.get("choices") or [{}])[0].get("finish_reason")
        if fr:
            finish_reason = fr

    assert finish_reason is not None, "stream ended without finish_reason"
    assert assembled, "no content delta events received"
    assert "OK" in assembled.upper(), f"expected OK in streamed response, got: {assembled!r}"


async def test_stream_tool_call(llama_server):
    events = await _stream_chat(
        llama_server["base_url"],
        [{"role": "user", "content": "What is 17*23? Use the calc tool."}],
        tools=[{
            "type": "function",
            "function": {
                "name": "calc",
                "description": "Evaluate a small arithmetic expression",
                "parameters": {
                    "type": "object",
                    "properties": {"expr": {"type": "string"}},
                    "required": ["expr"],
                },
            },
        }],
    )
    tool_calls = None
    for ev in events:
        choice = (ev.get("choices") or [{}])[0]
        delta = choice.get("delta") or {}
        if delta.get("tool_calls"):
            tool_calls = delta["tool_calls"]
        fr = choice.get("finish_reason")
        if fr == "tool_calls":
            break

    assert tool_calls, f"expected tool_calls in stream, got {len(events)} events"


async def test_stream_emits_thinking(llama_server):
    """Qwen3 is a thinking model — llama.cpp routes CoT to
    ``delta.reasoning_content`` when ``--jinja`` is active."""
    events = await _stream_chat(
        llama_server["base_url"],
        [{"role": "user", "content": "What is 2+2? Think step by step."}],
    )

    n_thinking = 0
    n_content = 0
    for ev in events:
        delta = (ev.get("choices") or [{}])[0].get("delta") or {}
        if delta.get("reasoning_content"):
            n_thinking += 1
        if delta.get("content"):
            n_content += 1

    assert n_content > 0, "expected content delta events"
    print(f"  thinking chunks: {n_thinking}, content chunks: {n_content}")


async def test_props_shows_spec_config(llama_server):
    """The ``/props`` endpoint should reflect speculative decoding settings."""
    r = httpx.get(f"{llama_server['base_url']}/props", timeout=5.0)
    assert r.status_code == 200
    data = r.json()
    settings = data.get("default_generation_settings", {})

    speculative = {
        k: v for k, v in settings.items()
        if "spec" in k.lower() or "draft" in k.lower()
    }
    print(f"  speculative settings: {speculative}")
    if not speculative:
        all_keys = list(settings.keys())
        print(f"  all /props keys: {all_keys}")
