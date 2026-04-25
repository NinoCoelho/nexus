"""Round-trip test for `nexus backup create` and `nexus backup restore`."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import typer.testing

from nexus.cli.backup_cmd import backup_app


def _seed_nexus_home(home: Path) -> None:
    (home / "vault").mkdir(parents=True, exist_ok=True)
    (home / "vault" / "note.md").write_text("hello\n")
    (home / "skills" / "demo").mkdir(parents=True, exist_ok=True)
    (home / "skills" / "demo" / "SKILL.md").write_text("---\nname: demo\n---\n# demo\n")
    (home / "config.toml").write_text("[agent]\n")
    (home / "cookies").mkdir(exist_ok=True)
    (home / "cookies" / "blob.bin").write_bytes(b"\x00" * 64)


def test_backup_create_then_restore_round_trip(tmp_path: Path) -> None:
    home = tmp_path / "nexus_home"
    home.mkdir()
    _seed_nexus_home(home)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    runner = typer.testing.CliRunner()

    with mock.patch("nexus.cli.backup_cmd._NEXUS_HOME", home), \
         mock.patch("nexus.cli.backup_cmd._DEFAULT_OUT", out_dir):
        archive = out_dir / "snap.tar.gz"
        result = runner.invoke(backup_app, ["create", "-o", str(archive)])
        assert result.exit_code == 0, result.output
        assert archive.exists()

        # Mutate state so we can prove the restore overwrites.
        (home / "vault" / "note.md").write_text("CHANGED\n")
        (home / "skills" / "demo" / "SKILL.md").unlink()

        result = runner.invoke(backup_app, ["restore", "--yes", str(archive)])
        assert result.exit_code == 0, result.output

        assert (home / "vault" / "note.md").read_text() == "hello\n"
        assert (home / "skills" / "demo" / "SKILL.md").exists()


def test_backup_create_skips_heavy_dirs_by_default(tmp_path: Path) -> None:
    home = tmp_path / "nexus_home"
    home.mkdir()
    _seed_nexus_home(home)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    archive = out_dir / "snap.tar.gz"

    runner = typer.testing.CliRunner()
    with mock.patch("nexus.cli.backup_cmd._NEXUS_HOME", home), \
         mock.patch("nexus.cli.backup_cmd._DEFAULT_OUT", out_dir):
        result = runner.invoke(backup_app, ["create", "-o", str(archive)])
        assert result.exit_code == 0, result.output

    import tarfile
    with tarfile.open(archive, "r:gz") as tf:
        names = tf.getnames()
    assert not any("cookies" in n.split("/") for n in names), names
