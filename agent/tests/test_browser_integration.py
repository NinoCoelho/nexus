"""Opt-in NBIE integration test — real managed Chrome, real CDP.

The unit suite covers the CDP plumbing with stubbed ``evaluate``/``send``;
this module runs the full perception loop against a real Chrome process
(the same one ``nexus browser`` would launch), over a throwaway local HTTP
server — no network, no external pages, no user profile.

The cycle mirrors the live smoke test that validated NBIE phases A–E:

    navigate → read (fields, redaction, contentHash, cache_hit)
    → ax_tree (password redaction) → find_element → click (verified)
    → page_signature → element_screenshot (PNG bytes) → list_tabs /
    switch_tab (WS re-point) → interactive_map → NBIE aggregate report

Opt-in because it boots a Chrome process (~seconds, real binary required):

    NEXUS_BROWSER_IT=1 uv run pytest tests/test_browser_integration.py -q

It uses a dedicated context key (``nbie-it``) on a high base port, so it
never touches the ``main`` browser or the daemon's instances.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from nexus.browser.manager import (
    BrowserManager,
    aggregate_nbie_events,
    find_chrome_binary,
)

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("NEXUS_BROWSER_IT") != "1",
        reason="opt-in: set NEXUS_BROWSER_IT=1 to run against a real Chrome",
    ),
    pytest.mark.skipif(
        find_chrome_binary() is None,
        reason="no Chrome/Chromium binary found",
    ),
]

# Throwaway two-page site. The password field carries a value attribute so
# redaction is *observable*: the value must never appear in any payload,
# while non-password fields stay readable.
PAGE_ONE = """<!doctype html><html><head><title>NBIE-IT One</title></head><body>
<h1>NBIE-IT PAGE ONE marker</h1>
<form onsubmit="return false">
  <label>Email <input id="email" type="email" value="agent@example.com"></label>
  <label>Password <input id="pass" type="password" value="s3cret-not-leak"></label>
  <button id="go" onclick="location.href='/page2?done=1'">Continue</button>
</form>
</body></html>"""

PAGE_TWO = """<!doctype html><html><head><title>NBIE-IT Two</title></head><body>
<h1>NBIE-IT PAGE TWO marker</h1>
<div id="out">output region for the element screenshot</div>
<button id="back" onclick="location.href='/'">Back</button>
</body></html>"""


class _SiteHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        body = {"/": PAGE_ONE, "/page2": PAGE_TWO}.get(path, "<h1>404</h1>").encode()
        self.send_response(200 if path in ("/", "/page2") else 404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # silence request logging
        pass


@pytest.fixture()
def site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SiteHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture()
def manager():
    # High base port: never collides with the daemon's 9223 ladder.
    mgr = BrowserManager(base_port=9377)
    yield mgr
    # stop() kills Chrome; the throwaway profile dir goes with it.
    try:
        asyncio.run(mgr.stop("nbie-it"))
    except OSError as exc:  # teardown best-effort (process already gone, etc.)
        print(f"teardown: stop failed: {exc}")
    shutil.rmtree(mgr.profile_dir("nbie-it"), ignore_errors=True)


async def test_nbie_integration_cycle(site: str, manager: BrowserManager) -> None:
    key = "nbie-it"

    # ── navigate ─────────────────────────────────────────────────────────
    nav = await manager.act(key, "navigate", {"url": site + "/"})
    assert nav["url"].startswith(site), nav

    # ── read: text-first perception, fields, redaction ───────────────────
    read1 = await manager.act(key, "read", {})
    assert "NBIE-IT PAGE ONE" in (read1.get("text") or "")
    assert read1.get("complete") in (True, False)  # the flag must exist
    assert read1.get("contentHash")
    assert read1.get("cache_hit") is False  # first read is never a hit
    fields = {f["selector"]: f for f in read1.get("fields") or []}
    assert fields["#pass"]["secret"] is True
    assert fields["#email"]["secret"] is False
    # the password value must appear nowhere in the payload
    assert "s3cret-not-leak" not in json.dumps(read1)

    # ── ax_tree: native a11y, compact shape, password never leaks ────────
    ax = await manager.act(key, "ax_tree", {"format": "json"})
    axj = json.dumps(ax)
    assert "s3cret-not-leak" not in axj  # hard invariant on every platform
    assert ax["nodes"] and ax["count"] >= 5
    for n in ax["nodes"]:
        assert isinstance(n["role"], str) and isinstance(n["name"], str)  # flattened
    pw_nodes = [n for n in ax["nodes"] if n["name"] == "Password"]
    assert pw_nodes, axj  # the password field is in the tree (by label)
    assert not any(n["value"] == "s3cret-not-leak" for n in ax["nodes"])
    # compact default: one ARIA line per node with refs
    axc = await manager.act(key, "ax_tree", {})
    assert axc["format"] == "compact" and axc["lines"]
    assert all("[ref=" in ln for ln in axc["lines"][:5])
    assert "s3cret-not-leak" not in json.dumps(axc)

    # ── cache-hit metric on an identical re-read ─────────────────────────
    read2 = await manager.act(key, "read", {})
    assert read2.get("cache_hit") is True

    # ── find_element → click with post-action verification ───────────────
    found = await manager.act(key, "find_element", {"role": "button", "text": "continue"})
    assert found.get("count", 0) >= 1 and found["elements"], found
    selector = found["elements"][0]["selector"]
    assert selector
    click = await manager.act(key, "click", {"selector": selector})
    assert click.get("url_changed") is True, click  # settle-poll caught the nav
    assert "page2" in (click.get("url") or "")
    assert click.get("state_changed") is True

    # ── page_signature: cheap fingerprint of the landed page ─────────────
    sig = await manager.act(key, "page_signature", {})
    assert "page2" in (sig.get("url") or "")
    assert sig.get("textHash")

    # ── element_screenshot: real clip → real PNG bytes ───────────────────
    shot = await manager.act(key, "element_screenshot", {"selector": "#out"})
    png = Path(shot["screenshot"]).expanduser()
    assert png.is_file(), shot
    assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"

    # ── list_tabs / switch_tab: WS re-point ──────────────────────────────
    tabs = await manager.act(key, "list_tabs", {})
    mine = next(t for t in tabs["tabs"] if (t["url"] or "").startswith(site))
    assert mine["targetId"]
    switched = await manager.act(key, "switch_tab", {"targetId": mine["targetId"]})
    assert switched.get("ok") is True and switched.get("switched") == mine["targetId"]

    # ── interactive_map: JS a11y approximation ───────────────────────────
    imap = await manager.act(key, "interactive_map", {})
    assert imap.get("ok") is True and imap.get("count", 0) >= 1

    # ── the NBIE report over this run's telemetry ────────────────────────
    report = aggregate_nbie_events(list(manager.nbie_events))
    totals = report["totals"]
    assert totals["actions"] >= 10
    assert totals["errors"] == 0
    assert totals["reads"] == 2 and totals["cache_hits"] == 1
    assert totals["cache_hit_rate"] == 0.5
    levels = report["levels"]
    assert levels.get("text", 0) >= 2  # read + dom-level probes
    assert levels.get("element", 0) >= 3  # ax_tree, find_element, page_signature…
    assert levels.get("vision", 0) == 1  # element_screenshot only
    assert levels.get("nav", 0) >= 2  # navigate + switch_tab
    assert report["keys"] == {key: totals["actions"]}
