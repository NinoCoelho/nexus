"""Tests for the shared probe library (NBIE).

Covers: byte-identity between the Python canonical copy (probes.py) and the
extension bundle (chrome/probes.js), and the CDP probe_expression builder.
"""

from __future__ import annotations

import json
from pathlib import Path

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
