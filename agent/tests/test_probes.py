"""Tests for the shared probe library (NBIE).

Covers: byte-identity between the Python canonical copy (probes.py) and the
extension bundle (chrome/probes.js), and the CDP probe_expression builder.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nexus.browser.probes import PROBES_JS, probe_expression


def test_probes_sync_with_extension() -> None:
    """The canonical Python copy must match the extension bundle byte-for-byte
    so CDP and the side panel perceive identically."""
    ext = Path(__file__).resolve().parents[2] / "chrome" / "probes.js"
    assert ext.exists(), f"extension probes missing at {ext}"
    assert PROBES_JS == ext.read_text()


def test_probe_expression_builds_dispatcher_call() -> None:
    expr = probe_expression("readPage", {"maxChars": 4000})
    # Defines self.__nexus_probes then invokes it with name + args.
    assert expr.startswith("self.__nexus_probes = function __nexus_probes")
    assert 'self.__nexus_probes("readPage", ' in expr
    payload = json.loads(expr.split('"readPage", ')[1].rstrip(")"))
    assert payload == {"maxChars": 4000}


def test_probe_expression_defaults_to_empty_args() -> None:
    expr = probe_expression("page_signature")
    assert 'self.__nexus_probes("page_signature", {})' in expr


_NODE_DISPATCH_SCRIPT = """
const fs = require('fs');
global.self = {};
eval(fs.readFileSync(process.argv[1], 'utf8'));  // defines self.__nexus_probes
global.document = {
  title: 'T',
  body: { innerText: 'hello world' },
  readyState: 'complete',
  querySelectorAll: () => [],
  querySelector: () => null,
};
global.location = { href: 'https://example.com/' };
global.window = {
  innerHeight: 900, innerWidth: 1440, scrollX: 0, scrollY: 0,
  devicePixelRatio: 1, scrollBy: () => {},
};
const run = (name) => self.__nexus_probes(name, {});
console.log(JSON.stringify({
  find_element_snake: run('find_element'),
  find_element_camel: run('findElement'),
  page_signature_snake: run('page_signature'),
  viewport_info_snake: run('viewport_info'),
  unknown: run('no_such_probe'),
}));
"""


def test_dispatcher_accepts_snake_and_camel_names() -> None:
    """The side panel passes tool action names (snake_case) while CDP passes
    camelCase probe names — the dispatcher must accept both. Regression for
    'unknown probe: find_element' (every panel probe action was broken since
    Phase B; Python stubs never executed the JS)."""
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    ext = Path(__file__).resolve().parents[2] / "chrome" / "probes.js"
    proc = subprocess.run(
        [node, "-e", _NODE_DISPATCH_SCRIPT, str(ext)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])

    # snake_case (panel surface) resolves to the same probes as camelCase
    assert out["find_element_snake"] == {"ok": True, "elements": [], "count": 0}
    assert out["find_element_snake"] == out["find_element_camel"]
    sig = out["page_signature_snake"]
    assert sig["ok"] is True and sig["url"] == "https://example.com/"
    assert sig["textHash"]  # hashed the stub body text
    assert out["viewport_info_snake"]["ok"] is True
    assert "unknown probe" in out["unknown"]["error"]


_DOM_FIXTURE_SCRIPT = """
// Richer DOM stub: dialog-first readPage, viewport-prioritized findElement,
// compact interactiveMap — with a visible dialog over an off-screen button.
const fs = require('fs');
global.self = {};
eval(fs.readFileSync(process.argv[1], 'utf8'));
global.NodeFilter = { SHOW_TEXT: 4, FILTER_ACCEPT: 1, FILTER_REJECT: 2, FILTER_SKIP: 3 };
const rect = (x, y, w, h) => ({ x, y, width: w, height: h, top: y, bottom: y + h, left: x, right: x + w });
const parent = { tagName: 'DIV', closest: () => null };
const texts = (vals) => vals.map((v) => ({ nodeValue: v, parentElement: parent }));
const dialog = {
  tagName: 'DIV', innerText: 'REQUEST API PERMISSIONS FLYOUT',
  _texts: texts(['REQUEST API PERMISSIONS FLYOUT']),
  getBoundingClientRect: () => rect(100, 50, 400, 300),
  getAttribute: () => null, getRootNode: () => global.document,
};
const hiddenBtn = {
  tagName: 'BUTTON', innerText: 'Hidden', disabled: false, type: undefined, value: '',
  getBoundingClientRect: () => rect(0, -500, 100, 36),
  getAttribute: () => null, getRootNode: () => global.document,
};
const visibleBtn = {
  tagName: 'BUTTON', innerText: 'Add a permission', disabled: false, type: undefined, value: '',
  getBoundingClientRect: () => rect(10, 10, 140, 36),
  getAttribute: () => null, getRootNode: () => global.document,
};
global.getComputedStyle = () => ({ visibility: 'visible', display: 'block' });
global.window = { innerHeight: 900, innerWidth: 1440, scrollX: 0, scrollY: 0, devicePixelRatio: 1 };
global.location = { href: 'https://portal.example/' };
global.document = {
  title: 'Portal',
  readyState: 'complete',
  body: { innerText: 'BACKGROUND TABLE TEXT', _texts: texts(['BACKGROUND TABLE TEXT']) },
  querySelector: () => null,
  querySelectorAll: (sel) => {
    if (sel.includes('dialog') || sel.includes('aria-modal')) return [dialog];
    if (sel.startsWith('button, a, input')) return [hiddenBtn, visibleBtn];
    return [];
  },
  createTreeWalker: (root, what, filt) => {
    const all = (root && root._texts) || [];
    let i = 0;
    return { nextNode() { while (i < all.length) { const n = all[i++]; if (!filt || filt.acceptNode(n) === 1) return n; } return null; } };
  },
};
const run = (name, args) => self.__nexus_probes(name, args || {});
const read = run('readPage', { maxChars: 8000, maxLinks: 80, maxFields: 40 });
const find = run('findElement', { limit: 10 });
const map = run('interactiveMap', { limit: 200 });
console.log(JSON.stringify({ read, find, map }));
"""


def test_probes_dialog_first_and_viewport_priority() -> None:
    """The M365-flyout fixes: dialogs extracted ahead of the background
    page, in-viewport matches ranked first, compact ARIA lines."""
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    ext = Path(__file__).resolve().parents[2] / "chrome" / "probes.js"
    proc = subprocess.run(
        [node, "-e", _DOM_FIXTURE_SCRIPT, str(ext)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])

    read = out["read"]
    assert read["ok"] is True
    # dialog-first: the flyout text leads, the background follows, labeled
    assert read["text"].startswith("[dialog]\nREQUEST API PERMISSIONS FLYOUT")
    assert "[page]\nBACKGROUND TABLE TEXT" in read["text"]
    assert read["complete"] is True
    assert read["contentHash"]  # hash covers the combined text

    find = out["find"]
    assert find["count"] == 2
    assert find["elements"][0]["text"] == "Add a permission"  # in-viewport first
    assert find["elements"][0]["inViewport"] is True
    assert find["elements"][1]["text"] == "Hidden"
    assert find["elements"][1]["inViewport"] is False

    imap = out["map"]
    assert imap["format"] == "compact" and imap["count"] == 2
    first, second = imap["lines"]
    assert '"Add a permission"' in first and "[ref=1]" in first
    assert "[offscreen]" in second and "[ref=2]" in second
    assert "box=10,10,140,36" in first  # role + name + box in one line
