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
