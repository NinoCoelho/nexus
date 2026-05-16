"""Nexus CLI — cookies subcommand group.

Manages the Chrome cookie export extension setup: installs the native
messaging host manifest so the extension can discover the Nexus port.
"""

from __future__ import annotations

import shutil
import stat
import sys
from pathlib import Path

import typer

cookies_app = typer.Typer(
    help="Cookie export setup for authenticated web scraping",
    no_args_is_help=True,
)

_NM_HOST_DIR = Path.home() / ".nexus" / "native-host"
_CHROME_NMH_DIR = Path.home() / "Library" / "Application Support" / "Google" / "Chrome" / "NativeMessagingHosts"


def _find_extension_dir() -> Path | None:
    candidates: list[Path] = []
    home = Path.home()
    candidates.append(home / ".nexus" / "extension")
    try:
        repo_root = Path(__file__).resolve().parent
        while repo_root != repo_root.parent:
            if (repo_root / "extension" / "manifest.json").is_file():
                candidates.append(repo_root / "extension")
                break
            repo_root = repo_root.parent
    except Exception:
        pass
    for base in (
        home / "Code" / "nexus",
        home / "code" / "nexus",
        home / "src" / "nexus",
        home / "projects" / "nexus",
        home / "dev" / "nexus",
    ):
        ext = base / "extension"
        if (ext / "manifest.json").is_file():
            candidates.append(ext)
    for c in candidates:
        if c.is_dir() and (c / "manifest.json").is_file():
            return c
    return None


@cookies_app.command("setup-chrome")
def setup_chrome(
    extension_id: str = typer.Option("", help="Override Chrome extension ID (only needed for non-standard installs)"),
) -> None:
    """Install the native messaging host for the Chrome cookie extension.

    Copies the NMH manifest + host script to ~/.nexus/native-host/ and
    symlinks into Chrome's NativeMessagingHosts directory.

    If you installed the Nexus .app, this was done automatically on first
    launch. This command is mainly for dev/non-app setups.

    After running this command, load the extension in Chrome:
    1. Open chrome://extensions
    2. Enable 'Developer mode' (top right)
    3. Click 'Load unpacked' and select the extension/ directory
    """
    ext_dir = _find_extension_dir()
    if ext_dir is None:
        typer.echo("error: could not locate the extension/ directory", err=True)
        typer.echo("Run this from the nexus repo root, or ensure extension/ exists.", err=True)
        raise typer.Exit(code=1)

    host_script_src = ext_dir / "native-host" / "nexus_cookie_host.py"
    if not host_script_src.exists():
        typer.echo(f"error: native host script not found at {host_script_src}", err=True)
        raise typer.Exit(code=1)

    _NM_HOST_DIR.mkdir(parents=True, exist_ok=True)

    host_script_dst = _NM_HOST_DIR / "nexus_cookie_host.py"
    shutil.copy2(host_script_src, host_script_dst)
    host_script_dst.chmod(host_script_dst.stat().st_mode | stat.S_IEXEC)

    manifest_tmpl = ext_dir / "native-host" / "com.nexus.cookies.json.tmpl"
    if not manifest_tmpl.exists():
        typer.echo(f"error: manifest template not found at {manifest_tmpl}", err=True)
        raise typer.Exit(code=1)

    manifest = manifest_tmpl.read_text()
    manifest = manifest.replace("HOST_SCRIPT_PATH", str(host_script_dst))
    if extension_id:
        manifest = manifest.replace(
            "chrome-extension://lponbchfnjkhpjpblledjklklcibjcnd/",
            f"chrome-extension://{extension_id}/",
        )

    manifest_dst = _NM_HOST_DIR / "com.nexus.cookies.json"
    manifest_dst.write_text(manifest)

    nmh_dirs: list[Path] = []
    if sys.platform == "darwin":
        nmh_dirs.append(_CHROME_NMH_DIR)
        for profile in ("Chrome Dev", "Chrome Beta", "Chromium"):
            nmh_dirs.append(
                Path.home() / "Library" / "Application Support" / "Google" / profile / "NativeMessagingHosts"
            )
    elif sys.platform == "linux":
        nmh_dirs.append(Path.home() / ".config" / "google-chrome" / "NativeMessagingHosts")
        nmh_dirs.append(Path.home() / ".config" / "chromium" / "NativeMessagingHosts")

    installed = False
    for nmh_dir in nmh_dirs:
        if not nmh_dir.is_dir():
            continue
        nmh_link = nmh_dir / "com.nexus.cookies.json"
        try:
            if nmh_link.is_symlink():
                if nmh_link.resolve() == manifest_dst.resolve():
                    continue
                nmh_link.unlink()
            elif nmh_link.exists():
                nmh_link.unlink()
            nmh_link.symlink_to(manifest_dst)
            typer.echo(f"Linked to {nmh_dir}")
            installed = True
        except OSError as e:
            typer.echo(f"warning: could not link in {nmh_dir}: {e}", err=True)

    if not installed:
        typer.echo(f"Manifest written to {manifest_dst}")
        typer.echo("Register this manifest with your browser's native messaging host directory.")

    typer.echo("")
    typer.echo("NMH installed. To load the extension in Chrome:")
    typer.echo("  1. Open chrome://extensions")
    typer.echo("  2. Enable 'Developer mode' (top right)")
    typer.echo(f"  3. Click 'Load unpacked' → select: {ext_dir}")
    typer.echo("  4. The Nexus Cookie Export icon will appear in your toolbar")
