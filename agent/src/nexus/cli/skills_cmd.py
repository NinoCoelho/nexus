"""Nexus CLI — skills subcommand group."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import typer

skills_app = typer.Typer(help="Skills commands", no_args_is_help=True)


@skills_app.command("list")
def skills_list() -> None:
    """List skills."""
    from ..config import SKILLS_DIR
    from ..skills.registry import SkillRegistry
    from rich.table import Table
    from rich.console import Console
    reg = SkillRegistry(SKILLS_DIR)
    table = Table(title="Skills")
    table.add_column("Name")
    table.add_column("Description")
    table.add_column("Trust")
    for s in reg.list():
        table.add_row(s.name, s.description, s.trust)
    Console().print(table)


@skills_app.command("view")
def skills_view(name: str = typer.Argument(...)) -> None:
    """View a skill."""
    from ..config import SKILLS_DIR
    from ..skills.registry import SkillRegistry
    reg = SkillRegistry(SKILLS_DIR)
    try:
        s = reg.get(name)
    except KeyError:
        typer.echo(f"Skill '{name}' not found.")
        raise typer.Exit(1)
    typer.echo(s.body)


@skills_app.command("install")
def skills_install(
    source: str = typer.Argument(..., help="Git URL, or local path to a skill directory or repo."),
    name: str = typer.Option(None, "--name", "-n", help="Override the installed skill name (defaults to source basename)."),
    subdir: str = typer.Option("", "--subdir", help="Path inside the repo if the skill isn't at the root."),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite an existing skill of the same name."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation when guard reports caution/dangerous patterns."),
) -> None:
    """Install a skill from a git URL or local path.

    Runs the same regex guard used for agent-authored skills before
    enabling the skill, and asks for confirmation when the verdict is
    caution or dangerous (unless --yes is passed).
    """
    from ..config import SKILLS_DIR
    from ..skills.guard import scan
    from ..skills.registry import SkillRegistry

    SKILLS_DIR.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as td:
        staging = Path(td)
        if "://" in source or source.startswith("git@"):
            typer.echo(f"Cloning {source}…")
            res = subprocess.run(
                ["git", "clone", "--depth", "1", source, str(staging / "repo")],
                capture_output=True,
                text=True,
            )
            if res.returncode != 0:
                typer.echo(f"git clone failed: {res.stderr.strip()}")
                raise typer.Exit(2)
            skill_root = staging / "repo"
        else:
            src_path = Path(source).expanduser().resolve()
            if not src_path.is_dir():
                typer.echo(f"Source path is not a directory: {src_path}")
                raise typer.Exit(2)
            skill_root = src_path

        if subdir:
            skill_root = skill_root / subdir

        if not (skill_root / "SKILL.md").is_file():
            typer.echo(f"No SKILL.md at {skill_root} — pass --subdir if it lives in a subfolder.")
            raise typer.Exit(2)

        # Use registry-style loading to get the declared name + body, then
        # run guard on body + every adjacent script-ish file.
        skill_md = (skill_root / "SKILL.md").read_text(encoding="utf-8")
        installed_name = name
        if not installed_name:
            import frontmatter  # type: ignore[import]
            installed_name = frontmatter.loads(skill_md).metadata.get("name") or skill_root.name

        scripts: list[str] = []
        for p in skill_root.rglob("*"):
            if p.is_file() and p.suffix in (".sh", ".py", ".js", ".ts"):
                try:
                    scripts.append(p.read_text(encoding="utf-8"))
                except OSError:
                    continue

        verdict = scan(skill_md, scripts=scripts)
        if verdict.level != "safe":
            typer.echo(f"Guard verdict: {verdict.level}")
            for f in verdict.findings:
                typer.echo(f"  [{f.pattern}] in {f.path}: {f.match}")
            if not yes:
                typer.confirm("Install anyway?", abort=True)

        dest = SKILLS_DIR / installed_name
        if dest.exists():
            if not force:
                typer.echo(f"Skill {installed_name!r} already exists. Pass --force to overwrite.")
                raise typer.Exit(1)
            shutil.rmtree(dest)

        shutil.copytree(skill_root, dest)
        # Don't preserve a .git from the staging clone.
        git_dir = dest / ".git"
        if git_dir.is_dir():
            shutil.rmtree(git_dir)

        # Reload to confirm it parses; if not, roll back.
        try:
            reg = SkillRegistry(SKILLS_DIR)
            reg.get(installed_name)
        except (KeyError, ValueError) as exc:
            shutil.rmtree(dest, ignore_errors=True)
            typer.echo(f"Skill failed to load after install: {exc}")
            raise typer.Exit(2)

        typer.echo(f"Installed skill {installed_name!r} ({verdict.level}).")


@skills_app.command("remove")
def skills_remove(name: str = typer.Argument(...)) -> None:
    """Remove a skill."""
    from ..config import SKILLS_DIR
    from ..skills.registry import SkillRegistry
    import shutil
    reg = SkillRegistry(SKILLS_DIR)
    try:
        reg.get(name)
    except KeyError:
        typer.echo(f"Skill '{name}' not found.")
        raise typer.Exit(1)
    skill_dir = SKILLS_DIR / name
    shutil.rmtree(skill_dir)
    typer.echo(f"Skill '{name}' removed.")
