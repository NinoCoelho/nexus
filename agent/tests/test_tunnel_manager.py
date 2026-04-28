"""Unit + middleware tests for the tunnel auth flow.

Covers the auth surface without spinning up a real cloudflared process: we
patch the provider so ``start()`` can pretend a tunnel is up, then probe the
middleware through ``TestClient``. The real cloudflared handshake is exercised
manually (see the verification section of the plan); CI doesn't have network
access to the Cloudflare edge anyway.

Flow exercised:
  1. ``start()`` produces a long token (cookie carrier) and a short code.
  2. The middleware lets the SPA + redeem + auth-status through the tunnel
     without a cookie; everything else 401s.
  3. ``POST /tunnel/redeem`` validates the code, then sets the cookie.
  4. With the cookie present, protected API surfaces unblock.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from nexus.main import app
from nexus.tunnel import get_manager
from nexus.tunnel.manager import TunnelManager, _generate_code, _normalize_code


def _fresh_client() -> TestClient:
    # Set the client host explicitly so the middleware's loopback check matches.
    return TestClient(app, client=("127.0.0.1", 12345))


@pytest.fixture(autouse=True)
def _reset_tunnel() -> Iterator[None]:
    mgr = get_manager()
    yield
    mgr._active = False
    mgr._token = None
    mgr._code = None
    mgr._public_url = None
    mgr._provider = None
    mgr._started_at = None
    mgr._process = None


def _fake_start_tunnel(url: str = "https://abc-words-here.trycloudflare.com"):
    """Build a patch return value matching cloudflared_provider.start_tunnel signature."""
    return MagicMock(), url


# ── manager unit tests ────────────────────────────────────────────────────


def test_status_inactive_by_default() -> None:
    s = get_manager().status()
    assert s.active is False
    assert s.code is None
    assert s.share_url is None


def test_start_produces_token_and_code() -> None:
    mgr = TunnelManager()
    with patch(
        "nexus.tunnel.manager.cloudflared_provider.start_tunnel",
        return_value=_fake_start_tunnel(),
    ):
        s = mgr.start(port=18989)
    assert s.active is True
    assert s.public_url == "https://abc-words-here.trycloudflare.com"
    assert s.share_url == "https://abc-words-here.trycloudflare.com/"
    # Code is 8 chars + a dash, formatted XXXX-XXXX.
    assert s.code is not None
    assert len(s.code) == 9 and s.code[4] == "-"
    with patch("nexus.tunnel.manager.cloudflared_provider.stop_tunnel"):
        mgr.stop()


def test_consume_code_returns_long_token() -> None:
    mgr = TunnelManager()
    with patch(
        "nexus.tunnel.manager.cloudflared_provider.start_tunnel",
        return_value=_fake_start_tunnel(),
    ):
        s = mgr.start(port=18989)
    long_token = mgr.consume_code(s.code)
    assert long_token is not None and len(long_token) >= 32
    # Multi-use until tunnel.stop()
    assert mgr.consume_code(s.code) == long_token
    # Token is what validate_token expects.
    assert mgr.validate_token(long_token) is True
    with patch("nexus.tunnel.manager.cloudflared_provider.stop_tunnel"):
        mgr.stop()


def test_consume_code_normalizes_dashes_and_case() -> None:
    mgr = TunnelManager()
    with patch(
        "nexus.tunnel.manager.cloudflared_provider.start_tunnel",
        return_value=_fake_start_tunnel(),
    ):
        s = mgr.start(port=18989)
    raw = (s.code or "").replace("-", "").lower()
    # Lowercase, no dashes — still valid.
    assert mgr.consume_code(raw) is not None
    # Wrong code rejected.
    assert mgr.consume_code("AAAA-BBBB") is None
    assert mgr.consume_code("") is None
    assert mgr.consume_code(None) is None
    with patch("nexus.tunnel.manager.cloudflared_provider.stop_tunnel"):
        mgr.stop()


def test_normalize_code_strips_noise() -> None:
    assert _normalize_code(" abcd-efgh ") == "ABCDEFGH"
    assert _normalize_code(None) == ""


def test_generated_code_uses_safe_alphabet() -> None:
    code = _generate_code()
    raw = code.replace("-", "")
    assert all(c.isupper() or c.isdigit() for c in raw)
    # No 0/O/1/I/L confusable characters.
    for ch in "01OIL":
        assert ch not in raw


def test_quick_url_regex_matches_cloudflared_stderr() -> None:
    """The provider scans cloudflared's stderr for the trycloudflare URL.

    Sample lines mirror the real format ('INF' log level, table-formatted
    banner) so a future cloudflared upgrade that breaks the format will fail
    here loudly instead of silently hanging on tunnel start.
    """
    from nexus.tunnel.cloudflared_provider import _QUICK_URL_RE

    sample = (
        "2026-04-27T12:34:56Z INF Requesting new quick Tunnel on trycloudflare.com...\n"
        "2026-04-27T12:34:57Z INF +-----------------------------------------+\n"
        "2026-04-27T12:34:57Z INF |  Your quick Tunnel has been created!    |\n"
        "2026-04-27T12:34:57Z INF |  https://magical-beaver-1234.trycloudflare.com  |\n"
        "2026-04-27T12:34:57Z INF +-----------------------------------------+\n"
    )
    m = _QUICK_URL_RE.search(sample)
    assert m is not None
    assert m.group(0) == "https://magical-beaver-1234.trycloudflare.com"


# ── middleware policy tests ────────────────────────────────────────────────


def test_middleware_loopback_bypass_no_tunnel() -> None:
    c = _fresh_client()
    assert c.get("/health").status_code == 200
    assert c.get("/sessions").status_code == 200


def test_proxied_protected_path_without_cookie_is_401(_simulated_active: Any) -> None:
    c = _fresh_client()
    r = c.get("/sessions", headers={"x-forwarded-for": "203.0.113.5"})
    assert r.status_code == 401


def test_proxied_static_path_without_cookie_is_allowed(_simulated_active: Any) -> None:
    """Loading the SPA shell on the phone must not require a cookie."""
    c = _fresh_client()
    # /assets/foo.js doesn't exist, but the middleware should let the request
    # through to the SPA fallback handler. We just assert it isn't 401.
    r = c.get("/assets/foo.js", headers={"x-forwarded-for": "203.0.113.5"})
    assert r.status_code != 401


def test_proxied_redeem_path_does_not_require_cookie(_simulated_active: Any) -> None:
    c = _fresh_client()
    # No cookie, no body — should reach the route (which 400s for missing code).
    r = c.post(
        "/tunnel/redeem",
        json={},
        headers={"x-forwarded-for": "203.0.113.5"},
    )
    assert r.status_code == 400


def test_proxied_auth_status_reports_redeem_required(_simulated_active: Any) -> None:
    c = _fresh_client()
    r = c.get("/tunnel/auth-status", headers={"x-forwarded-for": "203.0.113.5"})
    assert r.status_code == 200
    body = r.json()
    assert body == {"requires_redeem": True, "tunnel_active": True, "proxied": True}


def test_redeem_with_valid_code_sets_cookie_and_unlocks_api(
    _simulated_active_with_code: tuple[str, str],
) -> None:
    code, _long_token = _simulated_active_with_code
    c = _fresh_client()

    # 1. Phone hits /tunnel/redeem with the code.
    r = c.post(
        "/tunnel/redeem",
        json={"code": code},
        headers={"x-forwarded-for": "203.0.113.5"},
    )
    assert r.status_code == 200
    cookie = r.cookies.get("nexus_tunnel_token")
    assert cookie is not None and len(cookie) > 16

    # 2. With the cookie now seated, the protected API answers normally.
    r = c.get(
        "/sessions",
        headers={"x-forwarded-for": "203.0.113.5"},
        cookies={"nexus_tunnel_token": cookie},
    )
    assert r.status_code == 200

    # 3. /tunnel/auth-status now reports authenticated.
    r = c.get(
        "/tunnel/auth-status",
        headers={"x-forwarded-for": "203.0.113.5"},
        cookies={"nexus_tunnel_token": cookie},
    )
    assert r.json() == {"requires_redeem": False, "tunnel_active": True, "proxied": True}


def test_redeem_with_wrong_code_is_401(_simulated_active: Any) -> None:
    c = _fresh_client()
    r = c.post(
        "/tunnel/redeem",
        json={"code": "XXXX-YYYY"},
        headers={"x-forwarded-for": "203.0.113.5"},
    )
    assert r.status_code == 401


def test_redeem_rate_limit_kicks_in_after_repeated_failures(
    _simulated_active: Any,
) -> None:
    """After enough wrong attempts from the same IP, /tunnel/redeem returns 429."""
    # Reset bucket so this test is independent of others.
    from nexus.server.routes.tunnel import _rate_attempts
    _rate_attempts.clear()

    c = _fresh_client()
    headers = {"x-forwarded-for": "198.51.100.7"}
    last_status = 0
    for _ in range(12):
        r = c.post("/tunnel/redeem", json={"code": "AAAA-BBBB"}, headers=headers)
        last_status = r.status_code
        if last_status == 429:
            break
    assert last_status == 429


def test_admin_endpoints_reject_proxied_requests(
    _simulated_active_with_code: tuple[str, str],
) -> None:
    code, _ = _simulated_active_with_code
    c = _fresh_client()
    # Even after redeeming, admin endpoints stay loopback-only.
    redeem = c.post(
        "/tunnel/redeem",
        json={"code": code},
        headers={"x-forwarded-for": "203.0.113.5"},
    )
    cookie = redeem.cookies.get("nexus_tunnel_token")
    r = c.post(
        "/tunnel/start",
        headers={"x-forwarded-for": "203.0.113.5"},
        cookies={"nexus_tunnel_token": cookie},
    )
    assert r.status_code == 403


def test_admin_status_includes_code_on_loopback(_simulated_active: Any) -> None:
    c = _fresh_client()
    r = c.get("/tunnel/status")
    assert r.status_code == 200
    assert r.json()["code"] is not None


def test_proxied_request_via_cf_ray_header_requires_cookie(_simulated_active: Any) -> None:
    """Cloudflare edge sets cf-ray (no x-forwarded-for in some configs)."""
    c = _fresh_client()
    r = c.get("/sessions", headers={"cf-ray": "abcdef1234567890-IAD"})
    assert r.status_code == 401


def test_browser_navigation_to_protected_path_redirects_to_root(_simulated_active: Any) -> None:
    """Phone refreshes a deep link / opens a stale URL → 307 to ``/`` so the SPA
    can render the pairing screen, not a raw JSON 401."""
    c = _fresh_client()
    r = c.get(
        "/sessions/abc",
        headers={
            "x-forwarded-for": "203.0.113.5",
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
        follow_redirects=False,
    )
    assert r.status_code == 307
    assert r.headers.get("location") == "/"


def test_xhr_to_protected_path_still_returns_401(_simulated_active: Any) -> None:
    """Fetch-style XHR callers still get a 401 the SPA's interceptor can react to."""
    c = _fresh_client()
    r = c.get(
        "/sessions",
        headers={
            "x-forwarded-for": "203.0.113.5",
            "accept": "application/json",
        },
    )
    assert r.status_code == 401


def test_auth_status_reports_proxied_true_via_tunnel(_simulated_active: Any) -> None:
    """Tunnel-side requests should be tagged proxied=true so the SPA hides admin UI."""
    c = _fresh_client()
    r = c.get("/tunnel/auth-status", headers={"x-forwarded-for": "203.0.113.5"})
    assert r.status_code == 200
    assert r.json()["proxied"] is True


def test_auth_status_reports_proxied_false_on_loopback(_simulated_active: Any) -> None:
    """Direct loopback (no proxy headers) is the desktop owner's session."""
    c = _fresh_client()
    r = c.get("/tunnel/auth-status")
    assert r.status_code == 200
    assert r.json()["proxied"] is False


def test_auth_status_proxied_field_present_when_tunnel_inactive() -> None:
    """proxied flag is reported even when no tunnel is running, so the SPA's
    initial probe always has the answer (no second request needed)."""
    c = _fresh_client()
    r = c.get("/tunnel/auth-status")
    assert r.status_code == 200
    assert r.json() == {"requires_redeem": False, "tunnel_active": False, "proxied": False}


def test_security_headers_are_set() -> None:
    c = _fresh_client()
    r = c.get("/health")
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("x-frame-options") == "DENY"
    assert r.headers.get("referrer-policy") == "same-origin"
    assert "permissions-policy" in r.headers


def test_openapi_docs_are_disabled() -> None:
    """No FastAPI auto-docs exposure — single-user app, not a public API.

    The paths still resolve (the SPA catch-all serves the React shell), but the
    API surface (the JSON schema) is not exposed. We verify that by checking
    the response is HTML, not JSON.
    """
    c = _fresh_client()
    for p in ("/docs", "/redoc", "/openapi.json"):
        r = c.get(p)
        # If the SPA shell is built (e.g. on CI without ui/dist), we get 404 or
        # text/html; either way we should NEVER see application/json schema.
        ctype = r.headers.get("content-type", "")
        assert "application/json" not in ctype, (
            f"{p} leaked OpenAPI schema (content-type={ctype})"
        )


# ── fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def _simulated_active() -> Iterator[None]:
    """Pretend the tunnel is up so the middleware exercises the proxied branch."""
    mgr = get_manager()
    mgr._active = True
    mgr._token = "test-long-token-aaaaaaaaaaaaaaaaaaaa"
    mgr._code = "TEST-CODE"
    mgr._public_url = "https://abc-words.trycloudflare.com"
    mgr._provider = "cloudflare"
    yield
    mgr._active = False
    mgr._token = None
    mgr._code = None
    mgr._public_url = None
    mgr._provider = None


@pytest.fixture
def _simulated_active_with_code() -> Iterator[tuple[str, str]]:
    mgr = get_manager()
    long_token = "test-long-token-aaaaaaaaaaaaaaaaaaaa"
    code = "TEST-CODE"
    mgr._active = True
    mgr._token = long_token
    mgr._code = code
    mgr._public_url = "https://abc-words.trycloudflare.com"
    mgr._provider = "cloudflare"
    yield code, long_token
    mgr._active = False
    mgr._token = None
    mgr._code = None
    mgr._public_url = None
    mgr._provider = None
