"""Tests for the mmproj sidecar wiring in nexus.local_llm.manager.

We don't actually spawn llama-server here — the surface we need to lock
down is:

* ``is_mmproj_file`` correctly identifies projector sidecars.
* ``find_mmproj_sidecar`` picks the matching quant tier when multiple
  exist alongside a language GGUF.
* ``add_to_config`` adds a ``vision`` tag when the model came up with
  a projector.
* ``restart_local_models`` skips ``*mmproj*.gguf`` files when scanning
  for runnable models.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

from nexus.local_llm import manager


def test_is_mmproj_file_recognizes_projector_sidecars(tmp_path: Path) -> None:
    yes = tmp_path / "chandra-ocr-2.mmproj-q8_0.gguf"
    yes.touch()
    no = tmp_path / "chandra-ocr-2.Q8_0.gguf"
    no.touch()
    other = tmp_path / "model.bin"
    other.touch()
    assert manager.is_mmproj_file(yes) is True
    assert manager.is_mmproj_file(no) is False
    assert manager.is_mmproj_file(other) is False


def test_find_mmproj_sidecar_prefers_matching_quant(tmp_path: Path) -> None:
    """When multiple projectors live alongside the language GGUF, the
    one whose quant suffix matches wins so memory pressure stays in
    line with the user's pick."""
    lang = tmp_path / "chandra-ocr-2.Q8_0.gguf"
    lang.touch()
    (tmp_path / "chandra-ocr-2.mmproj-f16.gguf").touch()
    (tmp_path / "chandra-ocr-2.mmproj-q8_0.gguf").touch()

    sidecar = manager.find_mmproj_sidecar(lang)
    assert sidecar is not None
    assert sidecar.name == "chandra-ocr-2.mmproj-q8_0.gguf"


def test_find_mmproj_sidecar_returns_none_when_absent(tmp_path: Path) -> None:
    lang = tmp_path / "phi-3.5-mini-instruct-Q4_K_M.gguf"
    lang.touch()
    assert manager.find_mmproj_sidecar(lang) is None


def test_find_mmproj_sidecar_falls_back_to_first_match(tmp_path: Path) -> None:
    """No quant-tier match → still return *something* so the model
    doesn't silently come up text-only."""
    lang = tmp_path / "weird-name.gguf"  # no recognized quant tier
    lang.touch()
    sidecar_path = tmp_path / "weird-name.mmproj-f16.gguf"
    sidecar_path.touch()
    assert manager.find_mmproj_sidecar(lang) == sidecar_path


def test_add_to_config_tags_vision_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Vision-capable local models get a ``vision`` tag so chat-side
    capability detection (``capabilities_for_model_name``) treats them
    as image-capable without separate plumbing."""
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    cfg_path = tmp_path / ".nexus" / "config.toml"

    model_id = manager.add_to_config(
        slug="chandra-ocr-2-q8-0", port=51156, is_vision=True,
    )
    assert model_id == "local-chandra-ocr-2-q8-0/chandra-ocr-2-q8-0"

    raw = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    entries = raw.get("models", [])
    assert len(entries) == 1
    assert "vision" in entries[0]["tags"]
    assert "local" in entries[0]["tags"]


def test_add_to_config_omits_vision_tag_for_text_only_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    manager.add_to_config(slug="phi-3-5-mini", port=51200, is_vision=False)
    cfg_path = tmp_path / ".nexus" / "config.toml"
    raw = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    assert "vision" not in raw["models"][0]["tags"]


def test_restart_local_models_skips_mmproj_sidecars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The auto-restart sweep glob's `*.gguf` would otherwise treat each
    mmproj as its own runnable model — startup would either spam start
    failures or accidentally launch a server pointed at a projector."""
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    # Seed config with one local provider whose GGUF is text-only.
    manager.add_to_config(slug="text-model", port=51000)

    # Place both the language GGUF and a stray mmproj on disk.
    mdir = tmp_path / ".nexus" / "models" / "llm"
    mdir.mkdir(parents=True, exist_ok=True)
    (mdir / "text-model.gguf").touch()
    (mdir / "stray.mmproj-q8_0.gguf").touch()

    # Patch the actual server spawn — we only care about which slugs
    # restart is asked to start.
    started: list[str] = []

    def _fake_start(path: Path, ctx_size: int = 16384) -> manager.ServerHandle:
        started.append(path.name)

        class _DummyProc:
            def poll(self) -> None:
                return None

            def terminate(self) -> None:
                pass

        return manager.ServerHandle(
            proc=_DummyProc(),  # type: ignore[arg-type]
            port=51000,
            slug=manager.slugify(path.stem),
            model_path=path,
        )

    with patch.object(manager, "start", side_effect=_fake_start):
        manager.restart_local_models(models_dir=mdir)

    assert started == ["text-model.gguf"]
    assert "stray.mmproj-q8_0.gguf" not in started
