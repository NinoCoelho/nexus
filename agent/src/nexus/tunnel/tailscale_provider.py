"""tailscale provider — Tailscale Funnel and (tailnet-only) Tailscale Serve.

Unlike the Cloudflare Quick Tunnel, both run inside ``tailscaled``: we don't
own a child process, we just ask the ``tailscale`` CLI to publish
``https://<machine>.<tailnet>.ts.net`` → ``http://127.0.0.1:<port>`` and read
the public hostname back from ``tailscale status --json``.

Two flavors:

  * **funnel** (:func:`start_tunnel`) — reachable from the public internet.
    Gated by the same code/cookie flow as Cloudflare.
  * **serve** (:func:`start_serve`) — reachable only by devices authenticated
    into the user's tailnet (enforced by tailscaled at the network layer).
    The TunnelManager marks this mode trusted and skips the access code.

Requirements (surfaced as readable errors when missing):

  * the ``tailscale`` CLI on PATH (or the macOS .app bundle binary),
  * tailscaled running and logged in (``BackendState: Running``),
  * Funnel enabled on the tailnet (node attribute ``funnel`` — see
    https://tailscale.com/kb/1223/tailscale-funnel).

Auth works with **no middleware changes**: funnel is a reverse proxy, so
requests arrive on loopback carrying ``x-forwarded-for`` / ``x-forwarded-host``
headers, which ``_is_proxied`` already detects. While the TunnelManager has a
tailscale tunnel active, that traffic goes through the same cookie +
code-redemption gate as Cloudflare traffic.

Ownership model: ``start_tunnel`` replaces the funnel config's root entry
(``tailscale funnel --bg <port>``) and ``stop_tunnel`` resets the funnel
config (``tailscale funnel reset`` — the only teardown this CLI version
offers). If you manually funnel *other* services on this machine, stopping
Nexus sharing resets those too; re-add them afterwards.

Daemon restarts: funnel lives in tailscaled, so it survives a Nexus restart —
but the TunnelManager's secrets are in-memory. The orphaned funnel then fails
closed (proxied + inactive → 401) until sharing is re-activated from Nexus,
which re-applies the same config and mints a fresh code.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess

log = logging.getLogger(__name__)


class TailscaleError(RuntimeError):
    """Raised on any tailscale CLI / funnel failure."""


# macOS GUI install doesn't always symlink the CLI into /usr/local/bin.
_EXTRA_BINARY_PATHS = (
    "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
    "/usr/local/bin/tailscale",
    "/opt/homebrew/bin/tailscale",
)

_FUNNEL_START_TIMEOUT = 90.0  # first activation may provision HTTPS certs


def find_binary() -> str | None:
    """Locate the tailscale CLI, or None if not installed."""
    found = shutil.which("tailscale")
    if found:
        return found
    for candidate in _EXTRA_BINARY_PATHS:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def cli_available() -> bool:
    """True when the tailscale CLI can be found. Used by the UI pre-flight."""
    return find_binary() is not None


def _run(cmd: list[str], *, timeout: float = 30.0) -> subprocess.CompletedProcess[bytes]:
    binary = find_binary()
    if binary is None:
        raise TailscaleError(
            "tailscale CLI not found. Install Tailscale (e.g. `brew install tailscale` "
            "or https://tailscale.com/download) and make sure `tailscale` is on PATH.",
        )
    full = [binary, *cmd]
    log.info("running: %s", " ".join(full))
    return subprocess.run(  # noqa: S603 — fixed argv, tailscale binary from disk
        full,
        capture_output=True,
        timeout=timeout,
    )


def _tailnet_status() -> dict:
    """Parse `tailscale status --json`; raise readable errors when unusable."""
    try:
        proc = _run(["status", "--json"], timeout=15)
    except subprocess.TimeoutExpired as e:
        raise TailscaleError("tailscale status timed out — is tailscaled running?") from e
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        raise TailscaleError(
            "Could not talk to the Tailscale daemon. Is Tailscale running and logged in? "
            f"Underlying error: {stderr or f'exit {proc.returncode}'}",
        )
    try:
        return json.loads(proc.stdout.decode("utf-8", errors="replace") or "{}")
    except json.JSONDecodeError as e:
        raise TailscaleError(f"could not parse `tailscale status --json`: {e}") from e


def _public_url_from_status(status: dict) -> str:
    state = status.get("BackendState", "")
    if state != "Running":
        raise TailscaleError(
            f"Tailscale is not ready (BackendState: {state or 'unknown'}). "
            "Open the Tailscale app and make sure you're logged in.",
        )
    dns_name = (status.get("Self") or {}).get("DNSName") or ""
    dns_name = dns_name.strip().rstrip(".")
    if not dns_name:
        raise TailscaleError(
            "Could not determine this machine's Tailscale DNS name. "
            "Make sure Tailscale is connected to a tailnet.",
        )
    return f"https://{dns_name}"


def start_tunnel(*, port: int) -> tuple[None, str]:
    """Publish funnel for ``port`` and return ``(None, public_url)``.

    The ``None`` process slot keeps the manager's provider interface uniform
    with cloudflared; funnel state lives in tailscaled, not in a child we own.
    """
    url = _public_url_from_status(_tailnet_status())

    try:
        proc = _run(
            ["funnel", "--bg", "--yes", str(port)],
            timeout=_FUNNEL_START_TIMEOUT,
        )
    except subprocess.TimeoutExpired as e:
        raise TailscaleError(
            "tailscale funnel timed out. First-time activation can take a while "
            "(HTTPS cert provisioning) — try again in a minute.",
        ) from e
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        raise TailscaleError(
            f"tailscale funnel failed: {stderr or f'exit {proc.returncode}'} "
            "(Funnel must be enabled for your tailnet — see https://tailscale.com/kb/1223)",
        )

    log.info("tailscale funnel up: %s -> http://127.0.0.1:%d", url, port)
    return None, url


def stop_tunnel() -> None:
    """Tear the funnel config down. Best-effort, never raises."""
    _reset("funnel")


def start_serve(*, port: int) -> tuple[None, str]:
    """Publish ``tailscale serve`` (tailnet-only) for ``port``.

    Same mechanics as :func:`start_tunnel`, but private: tailscaled enforces
    at the network layer that only devices authenticated into the tailnet can
    connect, which is why the caller (TunnelManager) skips the access-code
    flow for this mode.

    Fail-closed guard: if a hand-configured *funnel* entry already proxies
    this same port, refuse — pairing public funnel traffic with a no-auth
    trust mode would publish Nexus to the internet without a code.
    """
    url = _public_url_from_status(_tailnet_status())
    _ensure_no_funnel_targets_port(port)

    try:
        proc = _run(["serve", "--bg", "--yes", str(port)], timeout=_FUNNEL_START_TIMEOUT)
    except subprocess.TimeoutExpired as e:
        raise TailscaleError("tailscale serve timed out — is tailscaled healthy?") from e
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        raise TailscaleError(
            f"tailscale serve failed: {stderr or f'exit {proc.returncode}'}",
        )

    log.info("tailscale serve up (tailnet-only): %s -> http://127.0.0.1:%d", url, port)
    return None, url


def stop_serve() -> None:
    """Tear the serve config down. Best-effort, never raises."""
    _reset("serve")


def _reset(kind: str) -> None:
    try:
        proc = _run([kind, "reset"], timeout=15)
        if proc.returncode != 0:
            stderr = proc.stderr.decode("utf-8", errors="replace").strip()
            log.warning("tailscale %s reset failed: %s", kind, stderr or proc.returncode)
    except Exception:
        log.exception("tailscale %s stop failed", kind)


def _ensure_no_funnel_targets_port(port: int) -> None:
    """Raise if any funnel handler proxies to ``port`` (see start_serve docstring).

    Best-effort: on any parse failure, assume it's safe — the common case is
    an empty/absent funnel config, and a false negative only means the user
    keeps a public funnel they configured themselves (still code-gated).
    """
    try:
        proc = _run(["funnel", "status", "--json"], timeout=15)
        if proc.returncode != 0:
            return
        config = json.loads(proc.stdout.decode("utf-8", errors="replace") or "{}")
    except Exception:
        return
    if not isinstance(config, dict):
        return
    for host_cfg in (config.get("Web") or {}).values():
        for handler in ((host_cfg or {}).get("Handlers") or {}).values():
            proxy = str((handler or {}).get("Proxy") or "")
            tail = proxy.rsplit(":", 1)[-1].strip("/")
            if proxy and tail == str(port):
                raise TailscaleError(
                    f"a public Tailscale Funnel is already forwarding to port {port}. "
                    "Tailnet-only mode would make that public traffic code-free. "
                    "Run `tailscale funnel reset` first, or use the Funnel provider.",
                )
