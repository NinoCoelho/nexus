"""Supervised dual-bind runner: loopback always + tailnet resilience.

Exercised with fakes (no sockets): a fake uvicorn.Server records starts,
a fake resolver drives the Tailscale lifecycle — down at boot, connect,
IP change (re-join), listener death, disconnect, global shutdown.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

import nexus.server.serve_runner as runner


class FakeServer:
    """uvicorn.Server double: serve() blocks until should_exit or crash."""

    def __init__(self, host: str, log: list) -> None:
        self.host = host
        self.should_exit = False
        self.crash = False
        self.log = log
        self.serve_count = 0

    async def serve(self) -> None:
        self.serve_count += 1
        self.log.append(f"start:{self.host}")
        while not self.should_exit:
            if self.crash:
                self.crash = False
                self.log.append(f"crash:{self.host}")
                return  # unexpected exit: should_exit stays False
            await asyncio.sleep(0.005)
        self.log.append(f"stop:{self.host}")


@pytest.fixture(autouse=True)
def _fast_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "TAILNET_POLL_SECONDS", 0.02)


def _make_factory(log: list, servers: dict[str, FakeServer]):
    def factory(host: str) -> FakeServer:
        server = FakeServer(host, log)
        servers[host] = server
        return server

    return factory


async def _wait_for(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition not reached in time")


@pytest.mark.asyncio
async def test_tailnet_down_at_boot_then_connects_and_rebinds() -> None:
    log: list[str] = []
    servers: dict[str, FakeServer] = {}
    state = {"ip": None}
    stop = threading.Event()

    async def run() -> None:
        await runner.run_servers(
            "nexus.main:app",
            port=18999,
            bind_spec="tailscale",
            stop_event=stop,
            server_factory=_make_factory(log, servers),
            resolver=lambda: state["ip"],
        )

    task = asyncio.create_task(run())

    # 1. Down at boot: loopback serves, no tailnet listener, no crash.
    await _wait_for(lambda: "start:127.0.0.1" in log)
    await asyncio.sleep(0.1)
    assert "100.1.2.3" not in [s.host for s in servers.values()]

    # 2. Tailscale connects → listener binds the resolved IP.
    state["ip"] = "100.1.2.3"
    await _wait_for(lambda: "start:100.1.2.3" in log)

    # 3. Re-joined with a different IP → old listener stops, new binds.
    state["ip"] = "100.9.9.9"
    await _wait_for(lambda: "start:100.9.9.9" in log)
    await _wait_for(lambda: "stop:100.1.2.3" in log)

    # 4. Same-IP listener death (serve() returns with should_exit still
    #    False — e.g. socket error) → restarted on the next poll. The
    #    restart bumps serve_count (incremented at serve() entry).
    current = servers["100.9.9.9"]
    before = current.serve_count
    current.crash = True
    await _wait_for(lambda: "crash:100.9.9.9" in log)
    await _wait_for(lambda: current.serve_count > before)

    # 5. Disconnect → runner stays alive on loopback (no crash, no exit).
    state["ip"] = None
    await asyncio.sleep(0.1)
    assert not task.done(), "runner must survive a tailscale disconnect"

    # 6. Global shutdown → loopback exits, run_servers returns.
    stop.set()
    await _wait_for(lambda: task.done(), timeout=3.0)
    assert "stop:127.0.0.1" in log


@pytest.mark.asyncio
async def test_loopback_only_spec_has_no_supervisor() -> None:
    log: list[str] = []
    servers: dict[str, FakeServer] = {}
    stop = threading.Event()

    async def run() -> None:
        await runner.run_servers(
            "nexus.main:app",
            port=18999,
            bind_spec="loopback",
            stop_event=stop,
            server_factory=_make_factory(log, servers),
            resolver=lambda: "100.5.5.5",
        )

    task = asyncio.create_task(run())
    await _wait_for(lambda: "start:127.0.0.1" in log)
    await asyncio.sleep(0.1)
    # Resolver says there IS a tailnet IP, but the spec never asked for it.
    assert not any(s.host != "127.0.0.1" for s in servers.values())
    stop.set()
    await _wait_for(lambda: task.done())


@pytest.mark.asyncio
async def test_explicit_ip_literal_is_served() -> None:
    log: list[str] = []
    servers: dict[str, FakeServer] = {}
    stop = threading.Event()

    async def run() -> None:
        await runner.run_servers(
            "nexus.main:app",
            port=18999,
            bind_spec="192.168.7.7",
            stop_event=stop,
            server_factory=_make_factory(log, servers),
            resolver=lambda: None,
        )

    task = asyncio.create_task(run())
    await _wait_for(lambda: "start:127.0.0.1" in log and "start:192.168.7.7" in log)
    stop.set()
    await _wait_for(lambda: task.done())


def test_resolve_bind_hosts_always_keeps_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    from nexus.server import bind as netbind

    assert netbind.resolve_bind_hosts("loopback") == ["127.0.0.1"]
    monkeypatch.setattr(netbind, "tailscale_ipv4", lambda: "100.101.102.103")
    assert netbind.resolve_bind_hosts("tailscale") == ["127.0.0.1", "100.101.102.103"]
    assert netbind.resolve_bind_hosts("10.0.0.5") == ["127.0.0.1", "10.0.0.5"]
    # Loopback literal doesn't duplicate.
    assert netbind.resolve_bind_hosts("127.0.0.1") == ["127.0.0.1"]
