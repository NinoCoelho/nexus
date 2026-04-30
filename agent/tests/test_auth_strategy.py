"""AuthStrategy + StaticBearerAuth + OpenAIProvider integration.

Verifies that the per-request header build path correctly merges the
strategy's headers and that aclose propagates to the strategy.
"""

from __future__ import annotations

import httpx

from nexus.agent.llm.auth import AuthStrategy, StaticBearerAuth
from nexus.agent.llm.openai import OpenAIProvider
from nexus.agent.llm.types import ChatMessage, Role


async def test_static_bearer_returns_authorization_header() -> None:
    auth = StaticBearerAuth("sk-abc")
    assert await auth.headers() == {"Authorization": "Bearer sk-abc"}


async def test_static_bearer_with_empty_key_returns_no_header() -> None:
    """An empty key (anonymous local server) yields no Authorization
    header so the upstream sees an unauthenticated request rather than
    ``Bearer `` which some servers reject."""
    auth = StaticBearerAuth("")
    assert await auth.headers() == {}


async def test_openai_provider_sends_auth_header_per_request() -> None:
    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        return httpx.Response(
            200,
            json={
                "id": "x",
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": "ok"},
                     "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    p = OpenAIProvider(
        base_url="http://fake/v1", auth=StaticBearerAuth("sk-abc"), model="m"
    )
    p._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[attr-defined]
    await p.chat([ChatMessage(role=Role.USER, content="hi")])
    assert captured, "no request reached transport"
    assert captured[0].headers.get("Authorization") == "Bearer sk-abc"
    assert captured[0].headers.get("Content-Type") == "application/json"


async def test_openai_provider_anonymous_omits_authorization() -> None:
    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        return httpx.Response(
            200,
            json={
                "id": "x",
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": "ok"},
                     "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    p = OpenAIProvider(
        base_url="http://fake/v1", auth=StaticBearerAuth(""), model="m"
    )
    p._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[attr-defined]
    await p.chat([ChatMessage(role=Role.USER, content="hi")])
    assert captured[0].headers.get("Authorization") is None


async def test_openai_provider_aclose_propagates_to_strategy() -> None:
    closed: dict[str, bool] = {"auth": False}

    class _CountingAuth(AuthStrategy):
        async def headers(self) -> dict[str, str]:
            return {}

        async def aclose(self) -> None:
            closed["auth"] = True

    p = OpenAIProvider(base_url="http://fake/v1", auth=_CountingAuth(), model="m")
    await p.aclose()
    assert closed["auth"] is True


class _RotatingAuth(AuthStrategy):
    """Strategy that returns a different token each call — proves headers
    are evaluated per request rather than cached at construction time."""

    def __init__(self) -> None:
        self._n = 0

    async def headers(self) -> dict[str, str]:
        self._n += 1
        return {"Authorization": f"Bearer token-{self._n}"}


async def test_openai_provider_evaluates_headers_per_request() -> None:
    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        return httpx.Response(
            200,
            json={
                "id": "x",
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": "ok"},
                     "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    p = OpenAIProvider(base_url="http://fake/v1", auth=_RotatingAuth(), model="m")
    p._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[attr-defined]
    await p.chat([ChatMessage(role=Role.USER, content="one")])
    await p.chat([ChatMessage(role=Role.USER, content="two")])
    assert len(captured) == 2
    assert captured[0].headers["Authorization"] == "Bearer token-1"
    assert captured[1].headers["Authorization"] == "Bearer token-2"
