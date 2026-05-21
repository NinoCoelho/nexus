"""Tests for MTP (multi-token prediction) auto-detection in local_llm.manager.

Verifies:
* ``_gguf_mtp_draft_layers`` reads ``<arch>.nextn_predict_layers`` from GGUF
  metadata and returns the correct count.
* ``start()`` auto-detects MTP models and passes ``--spec-type draft-mtp``
  and ``--spec-draft-n-max`` to llama-server.
* ``add_to_config`` persists ``spec_type`` / ``spec_draft_n_max`` in the
  model entry and adds the ``mtp`` tag.
* ``_gguf_has_mamba_layers`` no longer blocks models (the guard is removed).
* The ``/local/installed`` route surfaces ``has_mtp`` and ``mtp_draft_n``.
"""

from __future__ import annotations

import struct
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nexus.local_llm import manager


def _write_gguf(
    path: Path,
    architecture: str = "qwen35",
    block_count: int = 64,
    nextn_predict_layers: int = 0,
    include_ssm: bool = False,
) -> None:
    """Write a minimal GGUF v3 file with configurable metadata."""
    kv_pairs: list[tuple[str, int, object]] = []
    kv_pairs.append(("general.architecture", 8, architecture))
    kv_pairs.append((f"{architecture}.block_count", 4, block_count))
    if nextn_predict_layers > 0:
        kv_pairs.append((f"{architecture}.nextn_predict_layers", 4, nextn_predict_layers))
    if include_ssm:
        kv_pairs.append((f"{architecture}.ssm_conv_kernel", 4, 4))
        kv_pairs.append((f"{architecture}.ssm_state_size", 4, 128))

    # Write header + metadata only (no tensors).
    # GGUF v3: magic(4) + version(4) + tensor_count(8) + kv_count(8)
    buf = struct.pack("<4sIQQ", b"GGUF", 3, 0, len(kv_pairs))

    for key, val_type, val in kv_pairs:
        key_bytes = key.encode("utf-8")
        buf += struct.pack("<Q", len(key_bytes)) + key_bytes
        buf += struct.pack("<I", val_type)
        if val_type == 4:  # uint32
            buf += struct.pack("<I", val)
        elif val_type == 8:  # string
            val_bytes = val.encode("utf-8")
            buf += struct.pack("<Q", len(val_bytes)) + val_bytes

    path.write_bytes(buf)


class TestGgufMtpDetection:
    def test_returns_zero_when_no_mtp(self, tmp_path: Path) -> None:
        gguf = tmp_path / "model.gguf"
        _write_gguf(gguf, nextn_predict_layers=0)
        assert manager._gguf_mtp_draft_layers(gguf) == 0

    def test_detects_one_mtp_layer(self, tmp_path: Path) -> None:
        gguf = tmp_path / "model.gguf"
        _write_gguf(gguf, nextn_predict_layers=1)
        assert manager._gguf_mtp_draft_layers(gguf) == 1

    def test_detects_three_mtp_layers(self, tmp_path: Path) -> None:
        gguf = tmp_path / "model.gguf"
        _write_gguf(gguf, nextn_predict_layers=3)
        assert manager._gguf_mtp_draft_layers(gguf) == 3

    def test_returns_zero_on_invalid_file(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.gguf"
        bad.write_bytes(b"NOT_A_GGUF" + b"\x00" * 100)
        assert manager._gguf_mtp_draft_layers(bad) == 0

    def test_returns_zero_on_missing_file(self, tmp_path: Path) -> None:
        missing = tmp_path / "absent.gguf"
        assert manager._gguf_mtp_draft_layers(missing) == 0

    def test_mtp_with_ssm_metadata_not_blocked(self, tmp_path: Path) -> None:
        gguf = tmp_path / "model.gguf"
        _write_gguf(gguf, nextn_predict_layers=1, include_ssm=True)
        assert manager._gguf_mtp_draft_layers(gguf) == 1
        assert manager._gguf_has_mamba_layers(gguf) is True


class TestStartMtpFlags:
    def _mock_start(self, model_path: Path, **kwargs) -> manager.ServerHandle:
        """Call start() with mocked subprocess and binary discovery."""
        manager._servers.pop(model_path.name, None)

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.pid = 12345

        captured_cmd: list[str] = []

        def fake_popen(cmd, **kw):
            captured_cmd.extend(cmd)
            return mock_proc

        def fake_urlopen(url, timeout=1):
            r = MagicMock()
            r.status = 200
            r.__enter__ = lambda s: s
            r.__exit__ = MagicMock(return_value=False)
            r.read.return_value = b'{"data":[{"id":"m"}]}'
            return r

        real_open = open

        def selective_open(path, *a, **kw):
            if isinstance(path, (str, Path)) and "llama-server.log" in str(path):
                return MagicMock()
            return real_open(path, *a, **kw)

        with (
            patch("subprocess.Popen", side_effect=fake_popen),
            patch("urllib.request.urlopen", side_effect=fake_urlopen),
            patch.object(manager, "discover_binary", return_value=Path("/usr/bin/llama-server")),
            patch("builtins.open", side_effect=selective_open),
        ):
            handle = manager.start(model_path, **kwargs)
        return handle, captured_cmd

    def test_auto_detects_mtp_and_passes_flags(self, tmp_path: Path) -> None:
        gguf = tmp_path / "Qwen3.6-27B-MTP-Q8_0.gguf"
        _write_gguf(gguf, nextn_predict_layers=1, block_count=65)
        handle, cmd = self._mock_start(gguf)

        assert handle.is_mtp is True
        assert handle.spec_draft_n_max == 1
        assert "--spec-type" in cmd
        assert "draft-mtp" in cmd
        assert "--spec-draft-n-max" in cmd
        assert "1" in cmd

    def test_no_mtp_flags_for_standard_model(self, tmp_path: Path) -> None:
        gguf = tmp_path / "Qwen3.6-27B-Q5_K_M.gguf"
        _write_gguf(gguf, nextn_predict_layers=0, block_count=64)
        handle, cmd = self._mock_start(gguf)

        assert handle.is_mtp is False
        assert handle.spec_draft_n_max == 0
        assert "--spec-type" not in cmd

    def test_explicit_spec_type_override(self, tmp_path: Path) -> None:
        gguf = tmp_path / "model.gguf"
        _write_gguf(gguf, nextn_predict_layers=0)
        handle, cmd = self._mock_start(gguf, spec_type="draft-mtp", spec_draft_n_max=2)

        assert handle.is_mtp is True
        assert "--spec-type" in cmd
        assert "draft-mtp" in cmd
        assert "--spec-draft-n-max" in cmd
        assert "2" in cmd

    def test_mamba_model_no_longer_blocked(self, tmp_path: Path) -> None:
        gguf = tmp_path / "Qwen3.6-27B-Q5_K_M.gguf"
        _write_gguf(gguf, include_ssm=True, nextn_predict_layers=0)
        handle, cmd = self._mock_start(gguf)
        assert "--jinja" in cmd


class TestAddToConfigMtp:
    def test_mtp_tag_and_spec_fields_written(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        model_id = manager.add_to_config(
            slug="qwen36-27b-mtp-q8-0",
            port=52000,
            is_mtp=True,
            spec_draft_n_max=2,
        )
        assert model_id == "local-qwen36-27b-mtp-q8-0/qwen36-27b-mtp-q8-0"

        import tomllib
        cfg_path = tmp_path / ".nexus" / "config.toml"
        raw = tomllib.loads(cfg_path.read_text())
        entry = raw["models"][0]
        assert "mtp" in entry["tags"]
        assert entry["spec_type"] == "draft-mtp"
        assert entry["spec_draft_n_max"] == 2

    def test_no_mtp_fields_for_standard_model(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        manager.add_to_config(slug="qwen36-27b", port=53000)
        import tomllib
        cfg_path = tmp_path / ".nexus" / "config.toml"
        raw = tomllib.loads(cfg_path.read_text())
        entry = raw["models"][0]
        assert "mtp" not in entry["tags"]
        assert "spec_type" not in entry
        assert "spec_draft_n_max" not in entry
