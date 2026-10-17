"""Managed Chrome (computer-use) browser instances, one per context.

Contexts: ``main`` (the coordinator master chat + every unprojected
session) and ``project:<id>`` (every session in that project). Each
context gets a **dedicated-profile** Chrome process — separate
cookies/logins per project, never the user's daily profile — launched
lazily on first action and reused (probe-first) across app restarts.
Portability spec: ``browser-computer-use-spec.md`` at the repo root.

The whole CDP surface is ~15 methods over two channels:

* ``http://127.0.0.1:<port>/json/*`` — liveness probe, target discovery,
  new tab;
* ``ws://127.0.0.1:<port>/devtools/page/<targetId>`` — JSON-RPC framing
  with id-matched futures resolved by a single reader task.

The ``main`` instance intentionally reuses the legacy chrome-devtools
port (9223) and profile (``~/.nexus/chrome-profile``) so saved logins
survive and ``cdp_fill`` (site-credentials fill) keeps working against
the same Chrome.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import shutil
import subprocess
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Fixed logical viewport (launch flag --window-size). The UI's human
# takeover scales pixel clicks to this, deterministically regardless of DPI.
WINDOW_W = 1440
WINDOW_H = 900

PROBE_TIMEOUT = 1.5
LAUNCH_POLL_INTERVAL = 0.25
LAUNCH_TIMEOUT = 10.0
# A cold Chrome (fresh profile, first exec of the app bundle, busy machine)
# can outlive the in-turn wait without being dead: a background task keeps
# probing for ADOPT_TIMEOUT and connects, so the next action self-heals onto
# the SAME port instead of orphaning the Chrome and climbing the port ladder.
ADOPT_TIMEOUT = 60.0
ADOPT_POLL_INTERVAL = 1.0
CONNECT_ATTEMPTS = 2
CALL_TIMEOUT = 30.0
NAVIGATE_POLL_INTERVAL = 0.25
NAVIGATE_TIMEOUT = 10.0
WAIT_FOR_INTERVAL = 0.25
CLICK_SETTLE_SECONDS = 0.6
SCREENSHOT_RETENTION = 20
MAX_PORTS_ABOVE_BASE = 64

# Live-view stream (CDP Page.startScreencast → SSE). Frames are ack-paced:
# Chrome withholds the next frame until the previous one is acknowledged, so
# the cap is enforced by delaying the ack, never by dropping un-acked frames.
STREAM_HEARTBEAT_SECONDS = 15.0
STREAM_MIN_FRAME_INTERVAL = 0.25  # ~4 fps cap
STREAM_MAX_WIDTH = 1280
STREAM_MAX_HEIGHT = 800
STREAM_JPEG_QUALITY = 60

# Event sink signature: (context_key, event_payload_dict). Wired by the
# server to publish SessionEvent(kind="browser_event") — the manager
# itself stays session-agnostic.
EventSink = Callable[[str, dict[str, Any]], None]


class BrowserError(RuntimeError):
    """A browser action failed. Rendered to the LLM as {ok: false, …}."""


class BrowserStillStarting(BrowserError):
    """Launch exceeded the in-turn wait without the child exiting.

    The spawned Chrome may still be booting; a background adoption task
    keeps probing and connects when it comes up. Retryable by contract —
    callers should surface "retry shortly", not treat it as fatal.
    """


def find_chrome_binary() -> str | None:
    """Discovery order: macOS app bundles → Linux paths → ``which``."""
    home = Path.home()
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
        str(home / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/local/bin/google-chrome",
        "/usr/local/bin/chromium",
    ]
    for c in candidates:
        if Path(c).is_file():
            return c
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _sanitize(key: str) -> str:
    """Context key → filesystem-safe segment (``project:<uuid>`` → ``project-uuid``)."""
    seg = re.sub(r"[^A-Za-z0-9_.-]+", "-", key).strip("-.")
    return seg[:64] or "main"


def _ws_open(ws: Any) -> bool:
    """Socket liveness across websockets API generations.

    websockets>=14 removed ``.closed``; the new asyncio client exposes
    ``protocol.state``. Compare by enum *name* so we don't need the
    import (and tolerate either API).
    """
    if ws is None:
        return False
    state = getattr(getattr(ws, "protocol", None), "state", None)
    if state is not None:
        return getattr(state, "name", str(state)) == "OPEN"
    closed = getattr(ws, "closed", None)
    if closed is not None and hasattr(closed, "done"):
        return not closed.done()
    return True


async def _probe(port: int, timeout: float = PROBE_TIMEOUT) -> dict[str, Any] | None:
    """GET /json/version — returns the parsed body when a CDP endpoint answers."""
    import httpx

    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"http://127.0.0.1:{port}/json/version", timeout=timeout)
            if r.status_code == 200:
                return r.json()
    except Exception:
        pass
    return None


_READ_JS = (
    "JSON.stringify({url: location.href, title: document.title, "
    "text: (document.body ? document.body.innerText : '').slice(0, 8000), "
    "links: Array.from(document.querySelectorAll('a')).slice(0, 80) "
    "  .map(a => ({t: (a.textContent || '').trim().slice(0, 60), h: a.href})) "
    "  .filter(l => l.h)})"
)

_INFO_JS = "JSON.stringify({url: location.href, title: document.title})"

_CENTER_JS = (
    "((sel) => { const el = document.querySelector(sel); if (!el) return null; "
    "el.scrollIntoView({block: 'center'}); "
    "const r = el.getBoundingClientRect(); "
    "return {x: r.x + r.width / 2, y: r.y + r.height / 2}; })"
)

_FOCUS_JS = (
    "((sel) => { const el = document.querySelector(sel); if (!el) return false; "
    "el.focus(); return true; })"
)


class BrowserInstance:
    """One Chrome process + one persistent CDP connection for one context."""

    def __init__(self, *, key: str, port: int, profile_dir: Path, shots_dir: Path) -> None:
        self.key = key
        self.port = port
        self.profile_dir = profile_dir
        self.shots_dir = shots_dir
        self.last_active = time.monotonic()
        self.last_page: dict[str, Any] = {}
        self._ws: Any = None
        self._reader: asyncio.Task | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 0
        self._lock = asyncio.Lock()
        # our exec handle (None when probe-adopting an existing Chrome) —
        # a fast exit means the per-profile singleton handed off to another
        # process, which would otherwise hang the launch wait for nothing.
        self._proc: subprocess.Popen | None = None
        # background adoption task (set by the manager, cancelled in close()).
        self.adopt_task: asyncio.Task | None = None
        # CDP event fan-out: method name → subscriber queues (live stream).
        self._event_subs: dict[str, set[asyncio.Queue]] = {}
        self._screencast_on = False

    # ── lifecycle ────────────────────────────────────────────────────────────

    async def chrome_alive(self) -> bool:
        return await _probe(self.port) is not None

    def _launch(self) -> None:
        binary = find_chrome_binary()
        if binary is None:
            raise BrowserError(
                "no Chrome/Chromium binary found — install Google Chrome or Chromium"
            )
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            binary,
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={self.profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            f"--window-size={WINDOW_W},{WINDOW_H}",
            "about:blank",
        ]
        log.info("browser[%s]: launching %s on port %d", self.key, binary, self.port)
        self._proc = subprocess.Popen(  # noqa: S603 — fixed argv, discovered binary
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def proc_exited(self) -> bool:
        """True when our exec'd Chrome has already terminated (singleton
        handoff: the profile's existing Chrome absorbed us and we exited)."""
        return self._proc is not None and self._proc.poll() is not None

    async def ensure(self) -> None:
        """Lazy connect with transparent reconnect. Safe to call per action."""
        async with self._lock:
            if _ws_open(self._ws) and self._reader and not self._reader.done():
                # Cheap in-process check only — the reader task failing
                # clears _ws, and every send that errors triggers a
                # reconnect on the next ensure().
                return
            await self._close_ws()
            if await _probe(self.port) is None:
                if self.adopt_task and not self.adopt_task.done():
                    # Adoption already owns this boot — exec'ing again would
                    # singleton-handoff against the booting Chrome.
                    raise BrowserStillStarting(
                        f"Chrome on port {self.port} is still starting — "
                        "retry in a few seconds"
                    )
                self._launch()
                deadline = time.monotonic() + LAUNCH_TIMEOUT
                while time.monotonic() < deadline:
                    await asyncio.sleep(LAUNCH_POLL_INTERVAL)
                    if self.proc_exited():
                        # The profile's singleton absorbed our exec — another
                        # Chrome already runs on this user-data-dir (twin
                        # daemon, manual launch). Fail fast, not after 10s.
                        raise BrowserError(
                            "Chrome exited immediately after launch — the "
                            "profile is already in use by another Chrome "
                            "process; stop it first"
                        )
                    if await _probe(self.port) is not None:
                        break
                else:
                    # Still booting. The manager starts an adoption task;
                    # the port is already persisted, so the next action
                    # probes the SAME port and connects instead of
                    # allocating a new one (which would singleton-handoff
                    # against the booting Chrome and fail forever).
                    raise BrowserStillStarting(
                        f"Chrome on port {self.port} is still starting — "
                        "retry in a few seconds"
                    )
            await self._connect_with_retry()

    async def _connect_with_retry(self) -> None:
        """Pick a target + connect; one retry for the fresh-profile race
        where /json/list briefly reports no pages mid-setup."""
        last: BrowserError | None = None
        for _ in range(CONNECT_ATTEMPTS):
            try:
                target = await self._pick_target()
                await self._connect(target)
                return
            except BrowserError as exc:
                last = exc
                await self._close_ws()
                await asyncio.sleep(0.5)
        raise last if last is not None else BrowserError("connect failed")

    async def _pick_target(self) -> dict[str, Any]:
        """Prefer an about:blank page (avoids fighting a restored tab), else
        the last page; create a tab when there are none."""
        import httpx

        async with httpx.AsyncClient() as client:
            pages: list[dict[str, Any]] = []
            for _ in range(2):
                r = await client.get(f"http://127.0.0.1:{self.port}/json/list", timeout=5.0)
                r.raise_for_status()
                targets = r.json()
                pages = [
                    t
                    for t in targets
                    if t.get("type") == "page" and t.get("webSocketDebuggerUrl")
                ]
                if pages:
                    break
                # No pages: make one, then re-list.
                try:
                    await client.put(
                        f"http://127.0.0.1:{self.port}/json/new?about:blank", timeout=5.0
                    )
                except Exception as exc:
                    raise BrowserError(f"cannot create a tab on port {self.port}: {exc}") from exc
        for p in pages:
            if (p.get("url") or "").split("#", 1)[0] == "about:blank":
                return p
        return pages[-1]

    async def _connect(self, target: dict[str, Any]) -> None:
        import websockets

        ws = await websockets.connect(
            target["webSocketDebuggerUrl"], max_size=64 * 1024 * 1024
        )
        self._ws = ws
        self._reader = asyncio.create_task(
            self._read_loop(ws), name=f"browser-cdp-reader[{self.key}]"
        )
        if self._screencast_on:
            # Reconnect dropped the page session — restart the cast on the
            # new connection (outside the lock: start_screencast sends).
            asyncio.create_task(
                self._rearm_screencast(), name=f"browser-screencast-rearm[{self.key}]"
            )

    async def _rearm_screencast(self) -> None:
        if self.ws_open() and self._screencast_on:
            try:
                await self.start_screencast()
            except BrowserError:
                log.debug("browser[%s]: screencast re-arm failed", self.key, exc_info=True)

    async def _read_loop(self, ws: Any) -> None:
        try:
            async for raw in ws:
                data = json.loads(raw)
                mid = data.get("id")
                if mid is not None and mid in self._pending:
                    fut = self._pending.pop(mid)
                    if not fut.done():
                        fut.set_result(data)
                    continue
                method = data.get("method")
                if method:
                    self._dispatch_event(method, data)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        finally:
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(BrowserError("connection closed"))
            self._pending.clear()
            if self._ws is ws:
                self._ws = None

    # ── CDP event fan-out (live stream) ──────────────────────────────────────

    def _dispatch_event(self, method: str, data: dict[str, Any]) -> None:
        subs = self._event_subs.get(method)
        if not subs:
            return
        for q in list(subs):
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                # Slow consumer: keep the newest. Screencast is ack-paced so
                # this should not fire; if it does, the stale frame is the
                # one to drop — never the ack trigger.
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    q.put_nowait(data)
                except asyncio.QueueFull:
                    pass

    def subscribe(self, methods: list[str]) -> asyncio.Queue:
        """Receive CDP events (raw frames) on a bounded queue."""
        q: asyncio.Queue = asyncio.Queue(maxsize=16)
        for m in methods:
            self._event_subs.setdefault(m, set()).add(q)
        self.last_active = time.monotonic()
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        for subs in self._event_subs.values():
            subs.discard(q)

    def screencast_subscribers(self) -> int:
        return len(self._event_subs.get("Page.screencastFrame") or ())

    async def start_screencast(self) -> None:
        if self._screencast_on:
            return
        await self._send(
            "Page.startScreencast",
            {
                "format": "jpeg",
                "quality": STREAM_JPEG_QUALITY,
                "maxWidth": STREAM_MAX_WIDTH,
                "maxHeight": STREAM_MAX_HEIGHT,
                "everyNthFrame": 1,
            },
        )
        self._screencast_on = True

    async def stop_screencast(self) -> None:
        if not self._screencast_on:
            return
        self._screencast_on = False
        if self.ws_open():
            try:
                await self._send("Page.stopScreencast")
            except BrowserError:
                pass

    async def _close_ws(self) -> None:
        reader, ws = self._reader, self._ws
        self._reader = None
        self._ws = None
        if reader and not reader.done():
            reader.cancel()
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass

    async def close(self) -> None:
        task, self.adopt_task = self.adopt_task, None
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()
        async with self._lock:
            await self._close_ws()

    def ws_open(self) -> bool:
        return _ws_open(self._ws)

    # ── CDP primitives ───────────────────────────────────────────────────────

    async def _send(
        self, method: str, params: dict[str, Any] | None = None, *, timeout: float = CALL_TIMEOUT
    ) -> dict[str, Any]:
        await self.ensure()
        ws = self._ws
        if ws is None:
            raise BrowserError("connection closed")
        self._next_id += 1
        mid = self._next_id
        fut = asyncio.get_running_loop().create_future()
        self._pending[mid] = fut
        try:
            await ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
            data = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self._pending.pop(mid, None)
            raise BrowserError(f"{method} timed out after {timeout:.0f}s") from None
        except BrowserError:
            raise
        except Exception as exc:
            self._pending.pop(mid, None)
            raise BrowserError(f"connection closed ({exc})") from exc
        if "error" in data:
            raise BrowserError(str(data["error"].get("message") or data["error"]))
        return data.get("result") or {}

    async def evaluate(
        self, expression: str, *, await_promise: bool = False, timeout: float = CALL_TIMEOUT
    ) -> Any:
        res = await self._send(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": await_promise,
            },
            timeout=timeout,
        )
        exc = res.get("exceptionDetails")
        if exc:
            description = (exc.get("exception") or {}).get("description") or exc.get("text")
            raise BrowserError(str(description or "JS error"))
        return (res.get("result") or {}).get("value")

    def _note_page(self, result: dict[str, Any]) -> None:
        if isinstance(result.get("url"), str):
            self.last_page = {"url": result.get("url"), "title": result.get("title")}

    # ── actions (the spec §4 contract) ───────────────────────────────────────

    async def act(self, action: str, args: dict[str, Any]) -> dict[str, Any]:
        handler = getattr(self, f"_act_{action}", None)
        if handler is None:
            raise BrowserError(f"unknown action {action!r}")
        result = await handler(args)
        self.last_active = time.monotonic()
        self._note_page(result)
        return result

    async def _page_info(self) -> dict[str, Any]:
        raw = await self.evaluate(_INFO_JS)
        try:
            return json.loads(raw) if isinstance(raw, str) else {"url": raw}
        except (TypeError, json.JSONDecodeError):
            return {}

    async def _act_info(self, args: dict[str, Any]) -> dict[str, Any]:
        info = await self._page_info()
        return {"url": info.get("url"), "title": info.get("title")}

    async def _act_navigate(self, args: dict[str, Any]) -> dict[str, Any]:
        url = str(args.get("url") or "").strip()
        if not url:
            raise BrowserError("navigate requires url")
        if "://" not in url:
            url = f"https://{url}"
        await self._send("Page.enable")
        await self._send("Page.navigate", {"url": url})
        # Page.navigate resolves before commit — poll location.href until it
        # leaves about:blank (a fixed sleep is NOT enough on a cold profile).
        deadline = time.monotonic() + NAVIGATE_TIMEOUT
        href = "about:blank"
        while time.monotonic() < deadline:
            await asyncio.sleep(NAVIGATE_POLL_INTERVAL)
            try:
                href = await self.evaluate("location.href", timeout=2.0)
            except BrowserError:
                continue  # navigating targets drop evaluates mid-commit
            if href and href != "about:blank":
                break
        info = await self._page_info()
        if not info.get("url") or info["url"] == "about:blank":
            raise BrowserError(f"navigation to {url} did not commit within {NAVIGATE_TIMEOUT:.0f}s")
        return info

    async def _act_read(self, args: dict[str, Any]) -> dict[str, Any]:
        raw = await self.evaluate(_READ_JS)
        try:
            return json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise BrowserError(f"read failed: {exc}") from exc

    async def _act_dom_text(self, args: dict[str, Any]) -> dict[str, Any]:
        text = await self.evaluate(
            "(document.body ? document.body.innerText : '').slice(0, 12000)"
        )
        return {"url": (await self._page_info()).get("url"), "text": text or ""}

    async def _act_click(self, args: dict[str, Any]) -> dict[str, Any]:
        x, y = args.get("x"), args.get("y")
        selector = args.get("selector")
        if x is None or y is None:
            if not selector:
                raise BrowserError("click requires selector or x/y coordinates")
            center = await self.evaluate(f"({_CENTER_JS})({json.dumps(str(selector))})")
            if not center:
                raise BrowserError(f"element not found: {selector}")
            x, y = center["x"], center["y"]
        for evt_type in ("mousePressed", "mouseReleased"):
            await self._send(
                "Input.dispatchMouseEvent",
                {
                    "type": evt_type,
                    "x": float(x),
                    "y": float(y),
                    "button": "left",
                    "clickCount": 1,
                },
            )
        await asyncio.sleep(CLICK_SETTLE_SECONDS)
        return await self._act_info({})

    async def _act_type(self, args: dict[str, Any]) -> dict[str, Any]:
        text = args.get("text")
        if text is None:
            raise BrowserError("type requires text")
        selector = args.get("selector")
        if selector:
            focused = await self.evaluate(f"({_FOCUS_JS})({json.dumps(str(selector))})")
            if not focused:
                raise BrowserError(f"element not found: {selector}")
        # One call for the whole string — per-char key events are slow and
        # break IME/composition.
        await self._send("Input.insertText", {"text": str(text)})
        return await self._act_info({})

    async def _act_scroll(self, args: dict[str, Any]) -> dict[str, Any]:
        try:
            delta_y = float(args.get("delta_y") or 600)
        except (TypeError, ValueError):
            delta_y = 600.0
        await self._send(
            "Input.dispatchMouseEvent",
            {"type": "mouseWheel", "x": 400, "y": 400, "deltaX": 0, "deltaY": delta_y},
        )
        return await self._act_info({})

    async def _act_screenshot(self, args: dict[str, Any]) -> dict[str, Any]:
        res = await self._send("Page.captureScreenshot", {"format": "png"})
        data = res.get("data")
        if not data:
            raise BrowserError("screenshot returned no data")
        self.shots_dir.mkdir(parents=True, exist_ok=True)
        path = self.shots_dir / f"shot-{int(time.time() * 1000)}.png"
        path.write_bytes(base64.b64decode(data))
        _prune_screenshots(self.shots_dir)
        info = await self._act_info({})
        return {"screenshot": str(path), **info}

    async def _act_js(self, args: dict[str, Any]) -> dict[str, Any]:
        expression = str(args.get("expression") or "")
        if not expression:
            raise BrowserError("js requires expression")
        value = await self.evaluate(expression, await_promise=True)
        return {"value": value}

    async def _act_wait_for(self, args: dict[str, Any]) -> dict[str, Any]:
        selector = str(args.get("selector") or "")
        if not selector:
            raise BrowserError("wait_for requires selector")
        try:
            timeout_s = float(args.get("timeout_seconds") or 8)
        except (TypeError, ValueError):
            timeout_s = 8.0
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                if await self.evaluate(
                    f"!!document.querySelector({json.dumps(selector)})", timeout=2.0
                ):
                    return await self._act_info({})
            except BrowserError:
                pass
            await asyncio.sleep(WAIT_FOR_INTERVAL)
        raise BrowserError(f"wait_for: {selector!r} not found within {timeout_s:.0f}s")


ACTIONS = (
    "navigate",
    "read",
    "dom_text",
    "click",
    "type",
    "scroll",
    "screenshot",
    "js",
    "wait_for",
    "info",
)


def _prune_screenshots(shots_dir: Path, keep: int = SCREENSHOT_RETENTION) -> None:
    """Keep the newest ``keep`` shots — unbounded disk growth guard."""
    try:
        shots = sorted(
            (p for p in shots_dir.glob("shot-*.png") if p.is_file()),
            key=lambda p: p.name,
        )
        for old in shots[:-keep] if len(shots) > keep else ():
            old.unlink(missing_ok=True)
    except OSError:
        log.debug("browser: screenshot prune failed for %s", shots_dir, exc_info=True)


class BrowserManager:
    """Registry of per-context BrowserInstances + port allocation + idle reap.

    Module-level singleton via :func:`get_manager` (mirrors the
    coordinator service pattern) so the agent tool and the HTTP routes
    share instances.
    """

    def __init__(
        self,
        *,
        base_port: int = 9223,
        idle_shutdown_minutes: int = 30,
    ) -> None:
        self.base_port = base_port
        self.idle_shutdown_minutes = idle_shutdown_minutes
        self.event_sink: EventSink | None = None
        self._instances: dict[str, BrowserInstance] = {}
        self._ensure_lock = asyncio.Lock()
        self._reaper: asyncio.Task | None = None
        self._root = Path.home() / ".nexus" / "browser"
        self._registry: dict[str, dict[str, Any]] | None = None

    # ── context keys ─────────────────────────────────────────────────────────

    @staticmethod
    def main_key() -> str:
        return "main"

    @staticmethod
    def project_key(project_id: str) -> str:
        return f"project:{project_id}"

    # ── layout ───────────────────────────────────────────────────────────────

    def profile_dir(self, key: str) -> Path:
        if key == "main":
            # Legacy chrome-devtools profile — keeps saved logins and the
            # cdp_fill (site credentials) flow working on the same Chrome.
            return Path.home() / ".nexus" / "chrome-profile"
        return Path.home() / ".nexus" / "browser-profiles" / _sanitize(key)

    def shots_dir(self, key: str) -> Path:
        return self._root / "shots" / _sanitize(key)

    # ── instance registry (survives app restarts: probe-reuse) ───────────────

    @property
    def _registry_path(self) -> Path:
        return self._root / "instances.json"

    def _load_registry(self) -> dict[str, dict[str, Any]]:
        if self._registry is None:
            try:
                self._registry = json.loads(self._registry_path.read_text())
            except (OSError, json.JSONDecodeError):
                self._registry = {}
        return self._registry

    def _save_registry(self) -> None:
        try:
            self._registry_path.parent.mkdir(parents=True, exist_ok=True)
            self._registry_path.write_text(
                json.dumps(self._registry or {}, indent=2), encoding="utf-8"
            )
        except OSError:
            log.debug("browser: could not persist instance registry", exc_info=True)

    async def _alloc_port(self) -> int:
        used = {inst.port for inst in self._instances.values()}
        # Registry entries reserve their ports even when the Chrome is down:
        # handing key B a port recorded for key A would let B's Chrome answer
        # A's next probe — A would silently drive B's browser.
        used |= {int(e.get("port") or 0) for e in self._load_registry().values()}
        for offset in range(1, MAX_PORTS_ABOVE_BASE + 1):
            port = self.base_port + offset
            if port in used:
                continue
            if await _probe(port, timeout=0.5) is None:
                return port
        raise BrowserError(f"no free CDP port within {MAX_PORTS_ABOVE_BASE} of {self.base_port}")

    # ── public API ───────────────────────────────────────────────────────────

    async def ensure(self, key: str) -> BrowserInstance:
        async with self._ensure_lock:
            inst = self._instances.get(key)
            if inst is None:
                registry = self._load_registry()
                entry = registry.get(key) or {}
                if key == "main":
                    port = self.base_port
                else:
                    port = int(entry.get("port") or 0) or await self._alloc_port()
                profile_dir = Path(entry.get("profile_dir") or self.profile_dir(key))
                if key != "main":
                    # Persist BEFORE launching: a launch that outlives the
                    # in-turn wait leaves a booting Chrome on this port, and
                    # every retry must land on the SAME port/profile pair —
                    # a fresh port would exec a second Chrome against the
                    # same user-data-dir, which the profile singleton
                    # absorbs (instant exit) → permanent failure loop.
                    registry[key] = {"port": port, "profile_dir": str(profile_dir)}
                    self._registry = registry
                    self._save_registry()
                inst = BrowserInstance(
                    key=key,
                    port=port,
                    profile_dir=profile_dir,
                    shots_dir=self.shots_dir(key),
                )
                # Cache before the first ensure() so concurrent acts share
                # one boot (and _alloc_port sees the port as taken).
                self._instances[key] = inst
            try:
                await inst.ensure()
            except BrowserStillStarting:
                # Late-booting Chrome — adopt it in the background so the
                # next action (or the agent's retry) connects.
                self._start_adoption(key, inst)
                raise
            except BrowserError as exc:
                log.warning("browser[%s]: ensure failed on port %d: %s", key, inst.port, exc)
                raise
            self._start_reaper()
            return inst

    async def act(self, key: str, action: str, args: dict[str, Any]) -> dict[str, Any]:
        """Run one action; emit a browser_event on success or error."""
        try:
            inst = await self.ensure(key)
        except BrowserError as exc:
            self._emit(key, {"action": action, "error": str(exc)})
            raise
        try:
            params = {k: v for k, v in args.items() if k != "action"}
            result = await inst.act(action, params)
        except BrowserError as exc:
            self._emit(key, {"action": action, "error": str(exc)})
            raise
        except Exception as exc:  # noqa: BLE001 — surface as a tool error, not a crash
            self._emit(key, {"action": action, "error": str(exc)})
            raise BrowserError(str(exc)) from exc
        payload = {"action": action}
        payload.update({k: v for k, v in result.items() if k != "text"})
        self._emit(key, payload)
        return result

    def _emit(self, key: str, payload: dict[str, Any]) -> None:
        if self.event_sink is None:
            return
        try:
            self.event_sink(key, payload)
        except Exception:  # noqa: BLE001 — events are best-effort
            log.debug("browser: event sink failed", exc_info=True)

    # ── background adoption of late-booting Chromes ──────────────────────────

    def _start_adoption(self, key: str, inst: BrowserInstance) -> None:
        if inst.adopt_task and not inst.adopt_task.done():
            return
        inst.adopt_task = asyncio.create_task(
            self._adopt(key, inst), name=f"browser-adopt[{key}]"
        )

    async def _adopt(self, key: str, inst: BrowserInstance) -> None:
        """Keep probing a still-booting Chrome and connect when it's up.

        Without this, a Chrome that missed the in-turn window becomes an
        untracked orphan holding the port — invisible to the manager and
        sitting on the user's desktop forever.
        """
        deadline = time.monotonic() + ADOPT_TIMEOUT
        while time.monotonic() < deadline:
            await asyncio.sleep(ADOPT_POLL_INTERVAL)
            if await _probe(inst.port) is None:
                if inst.proc_exited():
                    break  # child died; nothing to adopt
                continue
            try:
                async with inst._lock:
                    if _ws_open(inst._ws):
                        return
                    await inst._close_ws()
                    await inst._connect_with_retry()
                log.info("browser[%s]: adopted late-starting Chrome on port %d", key, inst.port)
                self._emit(key, {"action": "adopted", "port": inst.port})
                return
            except BrowserError:
                continue
        log.warning(
            "browser[%s]: no DevTools endpoint on port %d within %.0fs — resetting",
            key, inst.port, ADOPT_TIMEOUT,
        )
        await self._reset_instance(key, inst)

    async def _reset_instance(self, key: str, inst: BrowserInstance) -> None:
        """Drop an unusable instance + kill its profile's processes so the
        next attempt starts clean (no orphan holding the profile/port)."""
        if self._instances.get(key) is inst:
            self._instances.pop(key, None)
        await inst.close()
        profile = str(inst.profile_dir)
        try:
            proc = await asyncio.create_subprocess_exec(
                "pkill", "-f", "--", f"--user-data-dir={profile}",
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            await proc.wait()
        except FileNotFoundError:
            pass
        registry = self._load_registry()
        if key != "main":  # main's port is fixed, not allocated
            registry.pop(key, None)
            self._registry = registry
            self._save_registry()

    async def screenshot(self, key: str) -> Path:
        result = await self.act(key, "screenshot", {})
        return Path(result["screenshot"])

    async def stream(self, key: str) -> AsyncIterator[dict[str, Any]]:
        """Live view for one context: SSE-ready event dicts.

        Yields ``{"type": "frame", "data": <base64 jpeg>}`` from CDP
        screencast (ack-paced, ~4 fps cap), ``{"type": "info", url/title}``
        periodically, and heartbeats. Watchers count as activity for the
        idle reaper. The generator ends (route closes the SSE stream) when
        Chrome dies; the client reconnects.
        """
        inst = await self.ensure(key)
        q = inst.subscribe(["Page.screencastFrame"])
        last_emit = 0.0
        frames = 0
        try:
            await inst.start_screencast()
            if inst.last_page:
                yield {"type": "info", **inst.last_page}
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=STREAM_HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    inst.last_active = time.monotonic()
                    yield {"type": "heartbeat"}
                    continue
                params = ev.get("params") or {}
                data = params.get("data")
                session_id = params.get("sessionId")
                if not data or session_id is None:
                    continue
                # Throttle by delaying the ack — Chrome withholds the next
                # frame until this one is acknowledged, so pacing here caps
                # the rate without ever dropping an un-acked frame.
                delay = STREAM_MIN_FRAME_INTERVAL - (time.monotonic() - last_emit)
                if delay > 0:
                    await asyncio.sleep(delay)
                try:
                    await inst._send("Page.screencastFrameAck", {"sessionId": session_id})
                except BrowserError:
                    continue  # reconnect in flight; the re-armed cast resyncs
                last_emit = time.monotonic()
                inst.last_active = last_emit
                frames += 1
                yield {"type": "frame", "data": data}
                if frames % 8 == 0 and inst.last_page:
                    yield {"type": "info", **inst.last_page}
        finally:
            inst.unsubscribe(q)
            if inst.screencast_subscribers() == 0:
                await inst.stop_screencast()

    async def status(self, key: str) -> dict[str, Any]:
        inst = self._instances.get(key)
        if inst is not None:
            connected = inst.ws_open()
            # A cached-but-disconnected instance may be mid-boot (adoption
            # pending) — probe before claiming "not running".
            running = connected or await _probe(inst.port) is not None
            return {
                "key": key,
                "connected": connected,
                "running": running,
                "port": inst.port,
                **({"url": inst.last_page.get("url"), "title": inst.last_page.get("title")}
                   if inst.last_page else {}),
            }
        entry = self._load_registry().get(key)
        if key == "main":
            # The main instance has a fixed port — probe it directly. The
            # registry is only an optimization for *project* port
            # allocation; relying on it here reported "not running" for a
            # perfectly alive Chrome (e.g. launched by the legacy vault
            # helper), which is why the UI showed nothing.
            port = int((entry or {}).get("port") or 0) or self.base_port
            if await _probe(port):
                return {"key": key, "connected": False, "running": True, "port": port}
        elif entry and await _probe(int(entry.get("port") or 0)):
            return {
                "key": key,
                "connected": False,
                "running": True,
                "port": int(entry["port"]),
            }
        return {"key": key, "connected": False, "running": False}

    async def list_instances(self) -> list[dict[str, Any]]:
        keys = list(dict.fromkeys([self.main_key(), *self._instances, *self._load_registry()]))
        return [await self.status(k) for k in keys]

    async def stop(self, key: str) -> dict[str, Any]:
        inst = self._instances.pop(key, None)
        if inst is not None:
            await inst.close()
        profile = str(self.profile_dir(key)) if inst is None or not inst.profile_dir else str(
            inst.profile_dir
        )
        # Profile-scoped kill — never matches the user's daily Chrome. The
        # "--" separator matters: the pattern starts with "--user-data-dir",
        # which getopt would otherwise swallow as an option.
        stopped = False
        try:
            proc = await asyncio.create_subprocess_exec(
                "pkill", "-f", "--", f"--user-data-dir={profile}",
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            await proc.wait()
            stopped = proc.returncode == 0
        except FileNotFoundError:
            stopped = False
        registry = self._load_registry()
        registry.pop(key, None)
        self._registry = registry
        self._save_registry()
        self._emit(key, {"action": "stopped"})
        return {"key": key, "stopped": True, "matched_process": stopped}

    async def stop_all(self) -> None:
        for key in list(self._instances):
            try:
                await self.stop(key)
            except Exception:  # noqa: BLE001
                log.debug("browser: stop failed for %s", key, exc_info=True)
        if self._reaper and not self._reaper.done():
            self._reaper.cancel()
            self._reaper = None

    # ── idle reaper ──────────────────────────────────────────────────────────

    def _start_reaper(self) -> None:
        if self.idle_shutdown_minutes <= 0 or (self._reaper and not self._reaper.done()):
            return
        self._reaper = asyncio.create_task(
            self._reap_loop(), name="browser-idle-reaper"
        )

    async def _reap_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            threshold = self.idle_shutdown_minutes * 60
            for key, inst in list(self._instances.items()):
                if time.monotonic() - inst.last_active < threshold:
                    continue
                log.info("browser[%s]: idle for %d min — stopping", key,
                         self.idle_shutdown_minutes)
                try:
                    await self.stop(key)
                except Exception:  # noqa: BLE001
                    log.debug("browser: idle stop failed for %s", key, exc_info=True)


_manager: BrowserManager | None = None


def set_manager(manager: BrowserManager | None) -> None:
    global _manager
    _manager = manager


def get_manager() -> BrowserManager | None:
    return _manager
