"""Multi-bind uvicorn runner: loopback always + supervised tailnet listener.

Why: the app's own ecosystem (bundled web UI, ``nexus pair``, the Chrome
extension, tunnel manager) talks to ``localhost:<port>`` — loopback must
never go away. The tailnet listener is the *additional* surface for paired
native clients, and it is the one that can vanish:

* Tailscale disconnected at boot → start loopback-only, keep probing.
* Interface removed while running → the listener dies or becomes
  unreachable; keep loopback, restart it when the network returns.
* Re-connected with a *different* IP (re-joined tailnet) → rebind.

The supervisor polls ``tailscale_ipv4()`` every ``TAILNET_POLL_SECONDS``
and converges: one uvicorn server per resolved address, plus one loopback
server that runs for the process's lifetime. Explicit IP literals get the
same supervision minus re-resolution (restart-on-death with backoff).

Shutdown: ``stop_event`` (set by CLI signal handlers in other threads) or
uvicorn's own signal capture (main thread — the watcher propagates
``should_exit`` to every server either way).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable

import uvicorn

from .bind import LOOPBACK, tailscale_ipv4

log = logging.getLogger(__name__)

TAILNET_POLL_SECONDS = 10.0
_CRASH_BACKOFF_SECONDS = 5.0

ServerFactory = Callable[[str, int], uvicorn.Server]


def default_server_factory(app_import: str, port: int) -> Callable[[str], uvicorn.Server]:
    def _make(host: str) -> uvicorn.Server:
        config = uvicorn.Config(app_import, host=host, port=port, reload=False)
        return uvicorn.Server(config)

    return _make


async def run_servers(
    app_import: str,
    *,
    port: int,
    bind_spec: str = "loopback",
    stop_event: threading.Event | None = None,
    server_factory: ServerFactory | None = None,
    resolver: Callable[[], str | None] | None = None,
) -> None:
    """Serve ``app_import`` on loopback (+ extra hosts) until stopped.

    ``bind_spec`` follows ``[server].bind`` semantics. Dynamic specs
    (``tailscale``) never fail startup — they degrade to loopback-only and
    pick the tailnet up when it appears.
    """
    make = server_factory or default_server_factory(app_import, port)
    resolver = resolver or tailscale_ipv4
    stop = stop_event or threading.Event()

    spec = (bind_spec or "loopback").strip().lower()
    dynamic = spec in ("tailscale", "tailnet", "vpn")

    static_extra: str | None = None
    if not dynamic and spec not in ("", "loopback", "localhost"):
        from .bind import resolve_bind_host

        # A bad literal is a config error — fail loudly.
        static_extra = resolve_bind_host(spec)

    loopback = make(LOOPBACK)
    servers: list[uvicorn.Server] = [loopback]
    servers_lock = threading.Lock()

    async def _exit_watcher() -> None:
        """Propagate should_exit from any server / stop_event to all."""
        while True:
            if stop.is_set():
                for s in servers:
                    s.should_exit = True
            else:
                with servers_lock:
                    snapshot = list(servers)
                if any(s.should_exit for s in snapshot):
                    for s in snapshot:
                        s.should_exit = True
            if loopback.should_exit:
                return
            await asyncio.sleep(0.25)

    async def _supervise(host: str) -> None:
        """Serve one static host forever, restarting on unexpected exit."""
        server = make(host)
        with servers_lock:
            servers.append(server)
        try:
            while not loopback.should_exit:
                await server.serve()
                if server.should_exit or loopback.should_exit:
                    return
                log.warning("listener on %s exited unexpectedly — restarting in %.0fs", host, _CRASH_BACKOFF_SECONDS)
                await asyncio.sleep(_CRASH_BACKOFF_SECONDS)
        finally:
            server.should_exit = True
            with servers_lock:
                if server in servers:
                    servers.remove(server)

    async def _supervise_tailnet() -> None:
        """Converge the tailnet listener toward the currently resolved IP."""
        bound: str | None = None
        server: uvicorn.Server | None = None
        task: asyncio.Task | None = None
        state = ""  # last announced state, for log-once transitions
        try:
            while not loopback.should_exit:
                ip = _safe_resolve(resolver)
                if ip is None:
                    if state != "down":
                        log.info(
                            "tailscale: not connected — tailnet listener idle "
                            "(loopback unaffected); will bind when it returns"
                        )
                        state = "down"
                elif ip != bound:
                    if server is not None:
                        # Detach the outgoing listener from the exit watcher
                        # BEFORE stopping it — a deliberately-stopping server
                        # must never look like a shutdown trigger (it would
                        # take loopback down with it).
                        with servers_lock:
                            if server in servers:
                                servers.remove(server)
                        server.should_exit = True
                        if task is not None:
                            await _swallow(task)
                    server = make(ip)
                    with servers_lock:
                        servers.append(server)
                    bound = ip
                    task = asyncio.create_task(server.serve())
                    log.info("tailscale: serving on http://%s:%d", ip, port)
                    state = ip
                elif task is not None and task.done() and not server.should_exit:
                    # Same IP, listener died (interface churn) — bring it back.
                    task = asyncio.create_task(server.serve())
                    log.info("tailscale: listener on %s restarted", ip)
                await asyncio.sleep(TAILNET_POLL_SECONDS)
        finally:
            if server is not None:
                server.should_exit = True
                if task is not None:
                    await _swallow(task)

    async def _swallow(task: asyncio.Task) -> None:
        try:
            await task
        except (Exception, asyncio.CancelledError):  # best-effort teardown
            log.debug("tailnet listener task ended", exc_info=True)

    def _safe_resolve(resolver: Callable[[], str | None]) -> str | None:
        try:
            return resolver()
        except Exception:  # noqa: BLE001 — resolution is advisory
            return None

    tasks: list[asyncio.Task] = [asyncio.create_task(loopback.serve())]
    if dynamic:
        tasks.append(asyncio.create_task(_supervise_tailnet()))
    elif static_extra and static_extra != LOOPBACK:
        tasks.append(asyncio.create_task(_supervise(static_extra)))

    watcher = asyncio.create_task(_exit_watcher())
    try:
        # Loopback is the process lifetime: when it exits, we all exit.
        # (A bind failure on loopback propagates — that's fatal by design.)
        await tasks[0]
    finally:
        watcher.cancel()
        for t in tasks:
            t.cancel()
