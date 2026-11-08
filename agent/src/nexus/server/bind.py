"""HTTP bind-host resolution — loopback by default, tailnet opt-in.

Historically the server always bound ``127.0.0.1`` and exposure happened
only through tunnels. ``[server].bind`` (or ``nexus serve --bind``) adds one
sanctioned direct-access mode: ``"tailscale"`` binds *only* the machine's
Tailscale IPv4 (100.64.0.0/10), so the listener is unreachable from the LAN
or the public internet — only from authenticated tailnet devices, which then
still have to present the pairing token (see :mod:`nexus.pairing` and
``LoopbackOrTokenMiddleware``).

Resolution order for ``"tailscale"``:

1. ``tailscale ip -4`` through the CLI discovery already used by the tunnel
   provider (authoritative, cross-platform, handles macOS GUI installs).
2. Interface-scan fallback (macOS/Linux): parse ``ifconfig`` / ``ip addr``
   for an address inside 100.64.0.0/10 — covers tailscaled running without
   an accessible CLI on PATH.

Anything else is an explicit IP (validated) or a hostname (resolved). The
default stays ``"loopback"`` — no behavior change for existing installs.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
import subprocess
import sys

log = logging.getLogger(__name__)

LOOPBACK = "127.0.0.1"

# CGNAT range Tailscale assigns node IPs from.
_TAILNET_V4 = ipaddress.ip_network("100.64.0.0/10")

_SYNONYMS = ("loopback", "localhost", "")
_TAILNET_KEYWORDS = ("tailscale", "tailnet", "vpn")


class BindError(RuntimeError):
    """Bind spec could not be resolved — message is user-facing guidance."""


def resolve_bind_hosts(spec: str = "loopback") -> list[str]:
    """Bind hosts for a spec — **loopback is always included**.

    The app's own ecosystem (bundled web UI, `nexus pair`, the Chrome
    extension, tunnel manager) talks to `localhost:<port>`; a tailnet-only
    bind would sever it. So: ``"loopback"`` → just loopback (the historical
    single listener, byte-identical behavior); ``"tailscale"`` / an explicit
    IP → loopback **plus** that address, served by the same app on the same
    port. Raises BindError for unusable specs.
    """
    value = (spec or "loopback").strip().lower()
    if value in _SYNONYMS:
        return [LOOPBACK]
    extra = resolve_bind_host(value)
    hosts = [LOOPBACK]
    if extra != LOOPBACK:
        hosts.append(extra)
    return hosts


def resolve_bind_host(spec: str = "loopback") -> str:
    """Turn a bind spec into a concrete bind host. Raises BindError."""
    value = (spec or "loopback").strip().lower()
    if value in _SYNONYMS:
        return LOOPBACK
    if value in _TAILNET_KEYWORDS:
        ip = tailscale_ipv4()
        if ip is None:
            raise BindError(
                "bind = \"tailscale\": no Tailscale IPv4 address found. "
                "Make sure Tailscale is installed and logged in "
                "(`tailscale status`), or set an explicit IP in "
                "[server].bind."
            )
        return ip
    # Explicit IP literal.
    try:
        addr = ipaddress.ip_address(value)
    except ValueError:
        pass
    else:
        return str(addr)
    # Hostname: resolve to one A record (prefer IPv4).
    try:
        infos = socket.getaddrinfo(value, None, socket.AF_INET)
    except socket.gaierror as exc:
        raise BindError(
            f"bind = {spec!r}: could not resolve a bind address ({exc})."
        ) from exc
    if not infos:
        raise BindError(f"bind = {spec!r}: no addresses resolved.")
    return infos[0][4][0]


def is_loopback_spec(spec: str) -> bool:
    return (spec or "loopback").strip().lower() in _SYNONYMS


# ── tailscale IPv4 discovery ─────────────────────────────────────────────────


def _tailscale_cli_ipv4() -> str | None:
    """``tailscale ip -4`` via the tunnel provider's CLI discovery."""
    from ..tunnel import tailscale_provider

    binary = tailscale_provider.find_binary()
    if binary is None:
        return None
    try:
        proc = subprocess.run(  # tailscale binary from disk, fixed argv
            [binary, "ip", "-4"],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    out = proc.stdout.decode("utf-8", errors="replace").strip()
    for line in out.splitlines():
        candidate = line.strip()
        try:
            addr = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if addr.version == 4:
            return candidate
    return None


def _scan_interfaces_ipv4() -> str | None:
    """Parse system interface output for a 100.64.0.0/10 address.

    Best-effort fallback for tailscaled without a usable CLI on PATH.
    Windows has no portable variant — the CLI path covers it.
    """
    if sys.platform.startswith("win"):
        return None
    parsers: list[tuple[list[str], re.Pattern[str]]] = []
    if sys.platform == "darwin":
        parsers.append((["ifconfig", "-a"], re.compile(r"\binet\s+(\d+\.\d+\.\d+\.\d+)")))
    else:
        parsers.append(
            (["ip", "-4", "-o", "addr"], re.compile(r"inet\s+(\d+\.\d+\.\d+\.\d+)"))
        )
        parsers.append((["ifconfig", "-a"], re.compile(r"\binet\s+(\d+\.\d+\.\d+\.\d+)")))
    for cmd, pattern in parsers:
        try:
            proc = subprocess.run(  # system binaries, fixed argv
                cmd,
                capture_output=True,
                timeout=10,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
            continue
        if proc.returncode != 0:
            continue
        text = proc.stdout.decode("utf-8", errors="replace")
        for match in pattern.finditer(text):
            candidate = match.group(1)
            try:
                if ipaddress.ip_address(candidate) in _TAILNET_V4:
                    return candidate
            except ValueError:
                continue
    return None


def tailscale_ipv4() -> str | None:
    """This machine's Tailscale IPv4, or None when not on a tailnet."""
    ip = _tailscale_cli_ipv4()
    if ip is not None:
        return ip
    return _scan_interfaces_ipv4()


def tailscale_serve_host(port: int) -> str | None:
    """Host of a tailnet-only ``tailscale serve`` HTTPS mapping to ``port``.

    ``tailscale serve`` publishes ``https://<machine>.<tailnet>.ts.net``
    (valid Let's Encrypt cert, tailnet-only) proxying to a local port.
    Returns the hostname when such a mapping exists; None otherwise.
    Public Funnel entries are deliberately NOT matched — the pairing QR
    must never point at the public internet.
    """
    import re as _re

    from ..tunnel import tailscale_provider

    binary = tailscale_provider.find_binary()
    if binary is None:
        return None
    try:
        proc = subprocess.run(  # tailscale binary from disk, fixed argv
            [binary, "serve", "status"],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    text = proc.stdout.decode("utf-8", errors="replace")
    lines = text.splitlines()
    host = None
    for line in lines:
        match = _re.search(r"https://([A-Za-z0-9.-]+)\s*\(tailnet only\)", line)
        if match:
            host = match.group(1).rstrip(".")
            break
    if host is None:
        return None
    if not any(f"127.0.0.1:{port}" in line for line in lines):
        return None
    return host


def bound_host_display(host: str) -> str:
    """Bracket IPv6 literals for display URLs; IPv4/pass-through unchanged."""
    try:
        if ipaddress.ip_address(host).version == 6:
            return f"[{host}]"
    except ValueError:
        pass
    return host
