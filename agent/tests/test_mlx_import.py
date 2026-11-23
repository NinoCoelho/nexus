"""Import-in-place: registering already-downloaded models via symlinks.

The flow exists so another tool's library (LM Studio's ``~/.lmstudio/models``,
a manual HF cache) can be pointed at once and every MLX snapshot under it
becomes activatable without re-downloading multi-GB weights. The contract:

* each snapshot is linked into ``MLX_MODELS_DIR`` under its slugified name;
* re-importing is idempotent (``already``, never a duplicate);
* a slug taken by a *different* directory is skipped, never overwritten;
* deleting an imported model removes only the link — the original files
  (the user's LM Studio library) must survive;
* scans nest up to a small depth (``publisher/model`` layouts) and never
  follows or re-imports symlinks.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nexus.local_llm import mlx_manager
from nexus.local_llm.mlx_snapshot import read_snapshot


@pytest.fixture
def fake_config(monkeypatch, tmp_path):
    """In-memory config.toml — delete_installed must never touch the real one.

    Same shape as the fixture in test_mlx_config_survives_cleanup.py (no
    cross-import: tests/ is not a package)."""
    state: dict = {"raw": {}}

    def _load():
        return tmp_path / "config.toml", state["raw"]

    def _save(_path, raw):
        state["raw"] = raw

    monkeypatch.setattr(mlx_manager, "_load_config", _load)
    monkeypatch.setattr(mlx_manager, "_save_config", _save)
    return state


def _make_snapshot(parent: Path, name: str, *, model_type: str = "qwen3") -> Path:
    d = parent / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text(
        f'{{"model_type": "{model_type}", "max_position_embeddings": 8192}}',
        encoding="utf-8",
    )
    (d / "model.safetensors").write_bytes(b"\0")
    return d


@pytest.fixture
def models_dir(monkeypatch, tmp_path):
    """Redirect MLX_MODELS_DIR at a temp dir (import_folder calls mkdir)."""
    root = tmp_path / "models"
    root.mkdir(parents=True)
    monkeypatch.setattr(mlx_manager, "MLX_MODELS_DIR", root)
    return root


def test_import_links_and_reads_through(models_dir, tmp_path):
    lib = tmp_path / "lmstudio"
    snap = _make_snapshot(lib / "publisher", "Test-Model-4bit")

    rows = mlx_manager.import_folder(str(lib))
    assert len(rows) == 1
    assert rows[0]["action"] == "imported"
    assert rows[0]["slug"] == "test-model-4bit"

    link = models_dir / "test-model-4bit"
    assert link.is_symlink()
    assert link.resolve() == snap.resolve()

    # list_installed / read_snapshot work through the link
    installed = mlx_manager.list_installed()
    assert [i.slug for i in installed] == ["test-model-4bit"]
    info = read_snapshot(link)
    assert info.slug == "test-model-4bit"


def test_reimport_is_idempotent(models_dir, tmp_path):
    lib = tmp_path / "lib"
    _make_snapshot(lib / "pub", "Model-A")

    first = mlx_manager.import_folder(str(lib))
    assert [r["action"] for r in first] == ["imported"]
    second = mlx_manager.import_folder(str(lib))
    assert [r["action"] for r in second] == ["already"]
    assert len(list(models_dir.iterdir())) == 1


def test_slug_collision_is_skipped_never_overwritten(models_dir, tmp_path):
    # A native (real) model already occupies the slug.
    real = models_dir / "model-a"
    real.mkdir(parents=True)
    (real / "config.json").write_text("{}", encoding="utf-8")
    (real / "model.safetensors").write_bytes(b"\0")

    lib = tmp_path / "lib"
    _make_snapshot(lib / "pub", "Model-A")
    rows = mlx_manager.import_folder(str(lib))
    assert rows[0]["action"] == "skipped"
    assert "name" in (rows[0]["reason"] or "")
    assert not (models_dir / "model-a").is_symlink()
    assert (real / "config.json").is_file()


def test_two_publishers_same_model_name(models_dir, tmp_path):
    """publisher/A and publisher2/A — first wins, second is skipped."""
    lib = tmp_path / "lib"
    _make_snapshot(lib / "pub1", "Shared")
    _make_snapshot(lib / "pub2", "Shared")
    rows = mlx_manager.import_folder(str(lib))
    actions = sorted(r["action"] for r in rows)
    assert actions == ["imported", "skipped"]
    link = models_dir / "shared"
    assert link.is_symlink()


def test_nested_scan_depth_and_no_symlink_following(models_dir, tmp_path):
    lib = tmp_path / "lib"
    deep = _make_snapshot(lib / "a" / "b", "Deep-Model")
    assert mlx_manager.import_folder(str(lib))[0]["action"] == "imported"

    # A symlink inside the scanned tree is never re-imported.
    (lib / "alias").symlink_to(deep, target_is_directory=True)
    again = mlx_manager.import_folder(str(lib))
    assert all(r["action"] == "already" for r in again)


def test_import_root_is_itself_a_snapshot(models_dir, tmp_path):
    snap = _make_snapshot(tmp_path, "Solo-Model")
    rows = mlx_manager.import_folder(str(snap))
    assert [r["action"] for r in rows] == ["imported"]
    assert (models_dir / "solo-model").is_symlink()


def test_import_missing_folder_reports_skip(models_dir, tmp_path):
    rows = mlx_manager.import_folder(str(tmp_path / "nope"))
    assert rows == [
        {"slug": None, "path": str(tmp_path / "nope"), "action": "skipped",
         "reason": "not a directory"}
    ]


def test_delete_imported_removes_link_only(models_dir, tmp_path, fake_config):
    """Deleting an imported model must never touch the original library."""
    lib = tmp_path / "lib"
    snap = _make_snapshot(lib / "pub", "Keep-Me")
    mlx_manager.import_folder(str(lib))
    link = models_dir / "keep-me"
    assert link.is_symlink()

    assert mlx_manager.delete_installed("keep-me") is True
    assert not link.exists()          # link gone…
    assert snap.is_dir()              # …original untouched
    assert (snap / "config.json").is_file()


def test_delete_native_still_rmtree(models_dir, tmp_path, fake_config):
    real = models_dir / "native"
    real.mkdir()
    (real / "config.json").write_text("{}", encoding="utf-8")
    assert mlx_manager.delete_installed("native") is True
    assert not real.exists()
