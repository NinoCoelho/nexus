"""Nexus CLI — backup / restore command.

Bundles the user's ~/.nexus/ directory into a single tar.gz so it can be
moved between machines or rolled back. Includes config, vault, sessions
DB, skills, and other writable state. Excludes large/derived directories
(``cookies/``, ``trajectories/`` rotated logs, ``models/`` cache) by
default — pass ``--full`` to include them.
"""

from __future__ import annotations

import datetime as _dt
import shutil
import tarfile
from pathlib import Path

import typer

backup_app = typer.Typer(help="Backup and restore ~/.nexus/.", no_args_is_help=True)

_NEXUS_HOME = Path("~/.nexus").expanduser()
_DEFAULT_OUT = Path("~/.nexus/backups").expanduser()

# Skipped unless --full is passed: caches and rotated/derived data.
_HEAVY_DIRS = {"cookies", "models", "graphrag_cache"}


def _iso_stamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


@backup_app.command("list")
def list_backups(
    directory: Path = typer.Option(
        None, "--dir", "-d",
        help="Directory to list (default: ~/.nexus/backups).",
    ),
) -> None:
    """List archives produced by `nexus backup create`, newest first."""
    from datetime import datetime
    from rich.console import Console
    from rich.table import Table

    target = (directory or _DEFAULT_OUT).expanduser()
    if not target.is_dir():
        typer.echo(f"No backup directory at {target}")
        return
    rows = sorted(
        (p for p in target.glob("*.tar.gz") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not rows:
        typer.echo(f"No archives in {target}")
        return
    table = Table(title=f"Backups in {target}")
    table.add_column("File")
    table.add_column("Size", justify="right")
    table.add_column("Modified")
    for p in rows:
        st = p.stat()
        size_mb = st.st_size / (1024 * 1024)
        when = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
        table.add_row(p.name, f"{size_mb:.1f} MB", when)
    Console().print(table)


@backup_app.command("create")
def create(
    output: Path = typer.Option(
        None, "--output", "-o",
        help="Output path for the archive. Defaults to ~/.nexus/backups/nexus-<ts>.tar.gz.",
    ),
    full: bool = typer.Option(
        False, "--full",
        help="Include caches (cookies/, models/, graphrag_cache/). Larger archive.",
    ),
) -> None:
    """Create a tar.gz of ~/.nexus/ in OUTPUT."""
    if not _NEXUS_HOME.exists():
        typer.echo(f"~/.nexus does not exist — nothing to back up.")
        raise typer.Exit(code=1)

    if output is None:
        _DEFAULT_OUT.mkdir(parents=True, exist_ok=True)
        output = _DEFAULT_OUT / f"nexus-{_iso_stamp()}.tar.gz"
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    def _filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
        # Always skip the backups dir itself so we don't recurse.
        rel = Path(info.name).parts
        if "backups" in rel:
            return None
        if not full:
            for part in rel:
                if part in _HEAVY_DIRS:
                    return None
        return info

    with tarfile.open(output, "w:gz") as tf:
        tf.add(_NEXUS_HOME, arcname=_NEXUS_HOME.name, filter=_filter)

    size_mb = output.stat().st_size / (1024 * 1024)
    typer.echo(f"Wrote {output} ({size_mb:.1f} MB)")


@backup_app.command("restore")
def restore(
    archive: Path = typer.Argument(..., help="Path to the .tar.gz produced by `nexus backup create`."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Replace ~/.nexus/ with the contents of ARCHIVE.

    The current ~/.nexus is moved to ~/.nexus.before-restore-<ts> first so
    nothing is lost on a botched restore.
    """
    archive = archive.expanduser().resolve()
    if not archive.is_file():
        typer.echo(f"Archive not found: {archive}")
        raise typer.Exit(code=1)

    if not yes:
        typer.confirm(
            f"This will replace {_NEXUS_HOME} with the contents of {archive.name}. Continue?",
            abort=True,
        )

    backup_dir = _NEXUS_HOME.with_name(f".nexus.before-restore-{_iso_stamp()}")
    if _NEXUS_HOME.exists():
        shutil.move(str(_NEXUS_HOME), str(backup_dir))
        typer.echo(f"Existing ~/.nexus moved to {backup_dir}")

    _NEXUS_HOME.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tf:
        # Resolve members safely — refuse anything that escapes the dest dir.
        dest_root = _NEXUS_HOME.parent.resolve()
        safe_members = []
        for m in tf.getmembers():
            target = (dest_root / m.name).resolve()
            try:
                target.relative_to(dest_root)
            except ValueError:
                typer.echo(f"Refusing unsafe path in archive: {m.name}")
                raise typer.Exit(code=2)
            safe_members.append(m)
        tf.extractall(dest_root, members=safe_members)

    # Archives created by `create` use arcname=_NEXUS_HOME.name. If the
    # archive comes from a different layout, the post-extract layout may
    # not match — fall back to extracting next to .nexus and let the user
    # rename, but at least surface the mismatch.
    if not _NEXUS_HOME.exists():
        typer.echo(
            f"Restore extracted to {dest_root} but no {_NEXUS_HOME.name}/ "
            f"directory was found at the expected location inside the archive."
        )
        raise typer.Exit(code=2)

    typer.echo(f"Restored to {_NEXUS_HOME}")
