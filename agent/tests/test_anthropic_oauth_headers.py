"""Capture the actual HTTP request the Anthropic SDK builds for each
auth mode, without hitting the network.

This is the test that would have caught the "OAuth path returns 200 with
zero events" symptom in PR-4 / claude-code adoption: it asserts the
headers we *think* we're sending — Authorization: Bearer + the
oauth-2025-04-20 beta flag, no x-api-key — are actually what the SDK
puts on the wire.

We patch the SDK's underlying httpx client with a MockTransport so the
SDK's own request-builder runs end-to-end (default headers, version
header, retries, json encoding). Anything the real network would see is
visible to us.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from nexus.agent.llm.anthropic import AnthropicProvider
from nexus.agent.llm.types import ChatMessage, Role


def _mock_anthropic_with(handler) -> AnthropicProvider:
    """Construct an AnthropicProvider, then swap its inner httpx client
    so all upstream calls land in ``handler`` instead of api.anthropic.com.

    Returns the provider — caller decides which auth shape to construct
    by passing args to AnthropicProvider before calling this helper.
    """
    raise NotImplementedError("inline below — kept here for shape doc")


def _capture_request(
    provider: AnthropicProvider,
) -> tuple[list[httpx.Request], httpx.Response]:
    """Patch the SDK's httpx client to capture outgoing requests.

    Returns (captured_requests_list, canned_response). The list mutates
    as the test runs.
    """
    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        # Respond with a minimal valid Anthropic non-streaming Message —
        # enough for the SDK to parse without raising. (Streaming gets
        # its own scenario below.)
        return httpx.Response(
            200,
            json={
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "model": "claude-haiku-4-5-20251001",
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
            request=req,
        )

    transport = httpx.MockTransport(handler)
    # The async SDK ships its own httpx client at provider._client._client.
    # Replace it with one wired to our transport so SDK retry, default
    # headers, json-encoding all run unchanged but never touch the network.
    inner = provider._client._client
    provider._client._client = httpx.AsyncClient(
        transport=transport,
        base_url=inner.base_url,
        timeout=inner.timeout,
    )
    return captured, None  # type: ignore[return-value]


@pytest.fixture
def api_key_provider() -> AnthropicProvider:
    return AnthropicProvider(api_key="sk-ant-test-key", model="claude-haiku-4-5-20251001")


@pytest.fixture
def oauth_provider() -> AnthropicProvider:
    return AnthropicProvider(
        oauth_access_token="oauth-access-token-test",
        model="claude-haiku-4-5-20251001",
    )


# ── api_key path ────────────────────────────────────────────────────────────


async def test_api_key_path_sends_x_api_key_not_bearer(
    api_key_provider: AnthropicProvider,
) -> None:
    captured, _ = _capture_request(api_key_provider)
    await api_key_provider.chat([ChatMessage(role=Role.USER, content="hi")])
    assert captured, "no request reached the mock transport"
    req = captured[0]
    assert req.headers.get("x-api-key") == "sk-ant-test-key"
    assert "Authorization" not in req.headers
    # Version header is set by the SDK regardless of auth mode.
    assert req.headers.get("anthropic-version"), "SDK didn't set anthropic-version"
    # API-key path must NOT carry the OAuth beta flag.
    assert "oauth-" not in (req.headers.get("anthropic-beta") or "")


# ── oauth path — the silent-200 case we're chasing ─────────────────────────


async def test_oauth_path_sends_bearer_not_x_api_key(
    oauth_provider: AnthropicProvider,
) -> None:
    """The big one. If this fails, that's the silent-empty-stream bug."""
    captured, _ = _capture_request(oauth_provider)
    await oauth_provider.chat([ChatMessage(role=Role.USER, content="hi")])
    assert captured, "no request reached the mock transport"
    req = captured[0]
    auth = req.headers.get("Authorization") or ""
    assert auth == "Bearer oauth-access-token-test", (
        f"OAuth path didn't set Bearer correctly. Got Authorization={auth!r}"
    )
    # x-api-key must be absent; if both are set Anthropic ignores Bearer.
    assert "x-api-key" not in req.headers, (
        "OAuth path also sent x-api-key — SDK is double-authing, Anthropic "
        "will prefer the (placeholder) x-api-key over Bearer."
    )


async def test_oauth_path_sends_oauth_beta_header(
    oauth_provider: AnthropicProvider,
) -> None:
    """Anthropic gates OAuth-authed requests on this header. Without it,
    the API has been observed to return 200 with empty content blocks
    instead of a clean 401 — exactly the silent-stream symptom."""
    captured, _ = _capture_request(oauth_provider)
    await oauth_provider.chat([ChatMessage(role=Role.USER, content="hi")])
    req = captured[0]
    beta = req.headers.get("anthropic-beta") or ""
    # The header may be a CSV of multiple beta flags; we just need ours
    # to be in the list.
    assert "oauth-2025-04-20" in beta, (
        f"anthropic-beta didn't carry the OAuth flag. Got {beta!r}"
    )


async def test_oauth_path_sends_anthropic_version(
    oauth_provider: AnthropicProvider,
) -> None:
    """anthropic-version is required on every call regardless of auth.
    The SDK should set it automatically — verifying explicitly because
    silent omission would also produce 200-with-empty results."""
    captured, _ = _capture_request(oauth_provider)
    await oauth_provider.chat([ChatMessage(role=Role.USER, content="hi")])
    req = captured[0]
    assert req.headers.get("anthropic-version"), (
        "anthropic-version missing from OAuth request"
    )


# ── streaming path: same headers must apply ─────────────────────────────────


# ── claude-code impersonation ──────────────────────────────────────────────


@pytest.fixture
def oauth_impersonate_provider() -> AnthropicProvider:
    return AnthropicProvider(
        oauth_access_token="oauth-access-token-test",
        model="claude-haiku-4-5-20251001",
        impersonate_claude_code=True,
    )


async def test_impersonate_sends_claude_code_user_agent(
    oauth_impersonate_provider: AnthropicProvider,
) -> None:
    """The whole point of the impersonation flag: User-Agent must look
    like the Claude Code CLI so Anthropic's Pro/Max rate-limit bucket
    applies. Without this header on Pro/Max bundles, Anthropic 429s
    aggressively even when the subscription quota is fine."""
    captured, _ = _capture_request(oauth_impersonate_provider)
    await oauth_impersonate_provider.chat([ChatMessage(role=Role.USER, content="hi")])
    req = captured[0]
    ua = req.headers.get("User-Agent") or ""
    assert "claude-cli" in ua, (
        f"impersonation enabled but User-Agent doesn't carry claude-cli. "
        f"Got User-Agent={ua!r}. The SDK may have overridden default_headers — "
        f"that's the failure mode this test is here to catch."
    )


async def test_impersonate_sends_x_app_header(
    oauth_impersonate_provider: AnthropicProvider,
) -> None:
    captured, _ = _capture_request(oauth_impersonate_provider)
    await oauth_impersonate_provider.chat([ChatMessage(role=Role.USER, content="hi")])
    req = captured[0]
    assert (req.headers.get("x-app") or "") == "cli", (
        f"impersonation enabled but x-app header missing/wrong. "
        f"Got x-app={req.headers.get('x-app')!r}"
    )


async def test_oauth_without_impersonate_does_not_send_x_app(
    oauth_provider: AnthropicProvider,
) -> None:
    """The default OAuth path (used by future real Anthropic OAuth flows)
    must NOT send the impersonation headers — only the local-creds path
    that explicitly opts in does."""
    captured, _ = _capture_request(oauth_provider)
    await oauth_provider.chat([ChatMessage(role=Role.USER, content="hi")])
    req = captured[0]
    assert "x-app" not in req.headers, (
        "Default OAuth path is sending x-app — should only happen when "
        "impersonate_claude_code=True"
    )
    ua = req.headers.get("User-Agent") or ""
    assert "claude-cli" not in ua, (
        f"Default OAuth path is sending claude-cli User-Agent. "
        f"Got {ua!r}"
    )


async def test_api_key_path_never_impersonates(
    api_key_provider: AnthropicProvider,
) -> None:
    """API-key users have zero ToS exposure to the impersonation path —
    confirm impersonation headers never go out for them."""
    captured, _ = _capture_request(api_key_provider)
    await api_key_provider.chat([ChatMessage(role=Role.USER, content="hi")])
    req = captured[0]
    assert "x-app" not in req.headers
    assert "claude-cli" not in (req.headers.get("User-Agent") or "")


async def test_oauth_streaming_request_carries_same_headers(
    oauth_provider: AnthropicProvider,
) -> None:
    """The streaming endpoint takes the same request shape; verify the
    SDK doesn't strip our default_headers in the streaming code path."""
    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        # Minimal SSE response so the SDK's stream parser doesn't raise.
        body = (
            "event: message_start\n"
            "data: {\"type\":\"message_start\",\"message\":{\"id\":\"x\","
            "\"type\":\"message\",\"role\":\"assistant\","
            "\"model\":\"claude-haiku-4-5-20251001\",\"content\":[],"
            "\"stop_reason\":null,\"usage\":{\"input_tokens\":1,\"output_tokens\":0}}}\n\n"
            "event: message_stop\n"
            "data: {\"type\":\"message_stop\"}\n\n"
        )
        return httpx.Response(
            200,
            content=body.encode("utf-8"),
            headers={"content-type": "text/event-stream"},
            request=req,
        )

    transport = httpx.MockTransport(handler)
    inner = oauth_provider._client._client
    oauth_provider._client._client = httpx.AsyncClient(
        transport=transport,
        base_url=inner.base_url,
        timeout=inner.timeout,
    )

    events_seen = 0
    async for _ev in oauth_provider.chat_stream(
        [ChatMessage(role=Role.USER, content="hi")],
    ):
        events_seen += 1

    assert captured, "streaming request didn't reach mock transport"
    req = captured[0]
    assert (req.headers.get("Authorization") or "").startswith("Bearer ")
    assert "x-api-key" not in req.headers
    assert "oauth-2025-04-20" in (req.headers.get("anthropic-beta") or "")
    # We don't assert specific event count — the empty SSE we wired is
    # legal (and exactly what the silent-200 bug looks like). The point
    # of THIS test is to verify headers regardless.
