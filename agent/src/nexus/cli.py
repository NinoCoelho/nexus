"""Nexus CLI — entry point: nexus."""

from __future__ import annotations

import sys
from typing import Any, Optional

import typer

app = typer.Typer(help="Nexus agent CLI", no_args_is_help=True)
config_app = typer.Typer(help="Config file commands", no_args_is_help=True)
providers_app = typer.Typer(help="Provider commands", no_args_is_help=True)
models_app = typer.Typer(help="Model commands", no_args_is_help=True)
routing_app = typer.Typer(help="Routing commands", no_args_is_help=True)
skills_app = typer.Typer(help="Skills commands", no_args_is_help=True)
sessions_app = typer.Typer(help="Session commands", no_args_is_help=True)
vault_app = typer.Typer(help="Vault commands", no_args_is_help=True)
kanban_app = typer.Typer(help="Kanban commands", no_args_is_help=True)

app.add_typer(config_app, name="config")
app.add_typer(providers_app, name="providers")
app.add_typer(models_app, name="models")
app.add_typer(routing_app, name="routing")
app.add_typer(skills_app, name="skills")
app.add_typer(sessions_app, name="sessions")
app.add_typer(vault_app, name="vault")
app.add_typer(kanban_app, name="kanban")


# ── serve ──────────────────────────────────────────────────────────────────────

@app.command()
def serve(
    port: int = typer.Option(18989, "--port", "-p"),
    host: str = typer.Option("127.0.0.1", "--host"),
) -> None:
    """Start the Nexus server."""
    import uvicorn
    uvicorn.run("nexus.main:app", host=host, port=port, reload=False)


# ── chat ───────────────────────────────────────────────────────────────────────

@app.command()
def chat(
    session: Optional[str] = typer.Option(None, "--session"),
    model: Optional[str] = typer.Option(None, "--model"),
    context: Optional[str] = typer.Option(None, "--context"),
    port: int = typer.Option(18989, "--port"),
) -> None:
    """Interactive chat loop (requires nexus serve running)."""
    import httpx
    from .config import PORT as DEFAULT_PORT

    base = f"http://127.0.0.1:{port}"

    # Check server health
    try:
        r = httpx.get(f"{base}/health", timeout=3)
        r.raise_for_status()
    except Exception:
        typer.echo(
            f"Cannot reach Nexus server at {base}. Run `nexus serve` in another terminal.",
            err=True,
        )
        raise typer.Exit(1)

    try:
        from rich.console import Console
        from rich.markdown import Markdown
        console = Console()
        use_rich = True
    except ImportError:
        use_rich = False

    try:
        from prompt_toolkit import PromptSession
        pt_session: Any = PromptSession()
        use_pt = True
    except ImportError:
        use_pt = False

    sid = session
    typer.echo("Nexus chat — type 'exit' or Ctrl-D to quit.\n")

    while True:
        try:
            if use_pt:
                line = pt_session.prompt("you> ")
            else:
                line = input("you> ")
        except (EOFError, KeyboardInterrupt):
            typer.echo("")
            break

        line = line.strip()
        if not line:
            continue
        if line.lower() in {"exit", "quit"}:
            break

        payload: dict = {"message": line}
        if sid:
            payload["session_id"] = sid
        if context:
            payload["context"] = context
        if model:
            payload["model"] = model

        try:
            resp = httpx.post(f"{base}/chat", json=payload, timeout=120)
            resp.raise_for_status()
        except Exception as exc:
            typer.echo(f"[error] {exc}", err=True)
            continue

        data = resp.json()
        sid = data.get("session_id", sid)

        # Print tool traces in gray
        for event in data.get("trace", []):
            name = event.get("name") or event.get("event", "")
            if name and name not in {"_meta", "iter", "reply", "tool_result"}:
                args = event.get("args", event.get("data", {}))
                if use_rich:
                    console.print(f"[dim]  tool: {name} {args}[/dim]")
                else:
                    typer.echo(f"  tool: {name} {args}")

        reply = data.get("reply", "")
        if use_rich:
            console.print(Markdown(reply))
        else:
            typer.echo(f"\n{reply}\n")


# ── config ─────────────────────────────────────────────────────────────────────

@config_app.command("path")
def config_path() -> None:
    """Print config file path."""
    from .config_file import CONFIG_PATH
    typer.echo(str(CONFIG_PATH))


@config_app.command("show")
def config_show() -> None:
    """Dump current config as TOML."""
    from .config_file import load, _cfg_to_dict
    import tomli_w
    cfg = load()
    sys.stdout.buffer.write(tomli_w.dumps(_cfg_to_dict(cfg)).encode())


@config_app.command("init")
def config_init() -> None:
    """Write default config to ~/.nexus/config.toml."""
    from .config_file import CONFIG_PATH, default_config, save
    if CONFIG_PATH.exists():
        typer.echo(f"Config already exists at {CONFIG_PATH}. Delete it first to reinit.")
        raise typer.Exit(1)
    save(default_config())
    typer.echo(f"Config written to {CONFIG_PATH}")


# ── providers ──────────────────────────────────────────────────────────────────

@providers_app.command("list")
def providers_list() -> None:
    """List providers."""
    import os
    from .config_file import load
    from . import secrets as _secrets
    from rich.table import Table
    from rich.console import Console
    cfg = load()
    table = Table(title="Providers")
    table.add_column("Name")
    table.add_column("Base URL")
    table.add_column("Type")
    table.add_column("Key Status")
    for name, p in cfg.providers.items():
        if p.type == "ollama":
            key_status = "anonymous"
        elif p.use_inline_key and _secrets.get(name):
            key_status = "configured (inline)"
        elif p.api_key_env and os.environ.get(p.api_key_env):
            key_status = f"configured (env: {p.api_key_env})"
        else:
            key_status = "MISSING"
        table.add_row(name, p.base_url or "(native)", p.type, key_status)
    Console().print(table)


@providers_app.command("add")
def providers_add(
    name: str = typer.Argument(...),
    base_url: str = typer.Option(..., "--base-url"),
    key_env: str = typer.Option("", "--key-env"),
) -> None:
    """Add a provider."""
    from .config_file import load, save, ProviderConfig
    cfg = load()
    cfg.providers[name] = ProviderConfig(base_url=base_url, api_key_env=key_env)
    save(cfg)
    typer.echo(f"Provider '{name}' added.")


@providers_app.command("set-key")
def providers_set_key(name: str = typer.Argument(...)) -> None:
    """Set an inline API key for a provider (stored in ~/.nexus/secrets.toml)."""
    import getpass
    from .config_file import load, save
    from . import secrets as _secrets
    cfg = load()
    if name not in cfg.providers:
        typer.echo(f"Provider '{name}' not found.")
        raise typer.Exit(1)
    try:
        key = typer.prompt("API key", hide_input=True)
    except Exception:
        key = getpass.getpass("API key: ")
    if not key.strip():
        typer.echo("No key entered — aborted.")
        raise typer.Exit(1)
    _secrets.set(name, key.strip())
    cfg.providers[name].use_inline_key = True
    save(cfg)
    typer.echo(f"Key stored for '{name}'. Run 'nexus serve' to pick it up.")


@providers_app.command("clear-key")
def providers_clear_key(name: str = typer.Argument(...)) -> None:
    """Remove the inline API key for a provider."""
    from .config_file import load, save
    from . import secrets as _secrets
    cfg = load()
    if name not in cfg.providers:
        typer.echo(f"Provider '{name}' not found.")
        raise typer.Exit(1)
    _secrets.delete(name)
    cfg.providers[name].use_inline_key = False
    save(cfg)
    typer.echo(f"Inline key cleared for '{name}'.")


@providers_app.command("fetch-models")
def providers_fetch_models(name: str = typer.Argument(...)) -> None:
    """Fetch available models from a provider's upstream API."""
    import asyncio
    import os
    import httpx
    from .config_file import load
    from . import secrets as _secrets
    from rich.table import Table
    from rich.console import Console

    cfg = load()
    if name not in cfg.providers:
        typer.echo(f"Provider '{name}' not found.")
        raise typer.Exit(1)

    p = cfg.providers[name]
    provider_type = p.type or ("anthropic" if name == "anthropic" else "openai_compat")

    async def _fetch() -> dict:
        async with httpx.AsyncClient(timeout=5.0) as client:
            if provider_type == "ollama":
                base = (p.base_url or "http://localhost:11434").rstrip("/")
                try:
                    r = await client.get(f"{base}/api/tags")
                    if r.status_code == 200:
                        data = r.json()
                        models = [m["name"] for m in data.get("models", [])]
                        return {"models": models, "ok": True, "error": None}
                    elif r.status_code == 404:
                        r2 = await client.get(f"{base}/v1/models")
                        if r2.status_code == 200:
                            data2 = r2.json()
                            models = [m["id"] for m in data2.get("data", [])]
                            return {"models": models, "ok": True, "error": None}
                        return {"models": [], "ok": False, "error": f"HTTP {r2.status_code}"}
                    return {"models": [], "ok": False, "error": f"HTTP {r.status_code}"}
                except httpx.ConnectError as exc:
                    return {"models": [], "ok": False, "error": f"connection refused — is Ollama running? ({exc})"}

            elif provider_type == "anthropic":
                api_key = ""
                if p.use_inline_key:
                    api_key = _secrets.get(name) or ""
                if not api_key and p.api_key_env:
                    api_key = os.environ.get(p.api_key_env, "")
                if not api_key:
                    return {"models": [], "ok": False, "error": "no API key configured"}
                r = await client.get(
                    "https://api.anthropic.com/v1/models",
                    headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                )
                if r.status_code != 200:
                    return {"models": [], "ok": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
                data = r.json()
                return {"models": [m["id"] for m in data.get("data", [])], "ok": True, "error": None}

            else:
                if not p.base_url:
                    return {"models": [], "ok": False, "error": "base_url not configured"}
                api_key = ""
                if p.use_inline_key:
                    api_key = _secrets.get(name) or ""
                if not api_key and p.api_key_env:
                    api_key = os.environ.get(p.api_key_env, "")
                if not api_key:
                    return {"models": [], "ok": False, "error": "no API key configured"}
                headers: dict[str, str] = {"Authorization": f"Bearer {api_key}"}
                base = p.base_url.rstrip("/")
                r = await client.get(f"{base}/models", headers=headers)
                if r.status_code != 200:
                    return {"models": [], "ok": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
                data = r.json()
                return {"models": [m["id"] for m in data.get("data", [])], "ok": True, "error": None}

    result = asyncio.run(_fetch())

    if not result["ok"]:
        typer.echo(f"Error: {result['error']}", err=True)
        raise typer.Exit(1)

    table = Table(title=f"Models from '{name}'")
    table.add_column("Model Name")
    for model_name in result["models"]:
        table.add_row(model_name)
    Console().print(table)


@providers_app.command("remove")
def providers_remove(name: str = typer.Argument(...)) -> None:
    """Remove a provider."""
    from .config_file import load, save
    cfg = load()
    if name not in cfg.providers:
        typer.echo(f"Provider '{name}' not found.")
        raise typer.Exit(1)
    del cfg.providers[name]
    save(cfg)
    typer.echo(f"Provider '{name}' removed.")


# ── models ─────────────────────────────────────────────────────────────────────

@models_app.command("list")
def models_list() -> None:
    """List configured models."""
    from .config_file import load
    from rich.table import Table
    from rich.console import Console
    cfg = load()
    table = Table(title="Models")
    table.add_column("ID")
    table.add_column("Provider")
    table.add_column("Model Name")
    table.add_column("Tags")
    table.add_column("Strengths")
    for m in cfg.models:
        s = m.strengths
        strengths_str = f"spd={s.speed} cost={s.cost} rsn={s.reasoning} code={s.coding}"
        table.add_row(m.id, m.provider, m.model_name, ",".join(m.tags), strengths_str)
    Console().print(table)


@models_app.command("add")
def models_add(
    id: str = typer.Argument(...),
    provider: str = typer.Option(..., "--provider"),
    model_name: str = typer.Option(..., "--model"),
    tags: str = typer.Option("", "--tags"),
    strengths: str = typer.Option("", "--strengths"),
) -> None:
    """Add a model."""
    from .config_file import load, save, ModelEntry, ModelStrengths
    cfg = load()
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    s_dict: dict[str, int] = {}
    for part in strengths.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            s_dict[k.strip()] = int(v.strip())
    m = ModelEntry(
        id=id,
        provider=provider,
        model_name=model_name,
        tags=tag_list,
        strengths=ModelStrengths(**s_dict),
    )
    cfg.models.append(m)
    save(cfg)
    typer.echo(f"Model '{id}' added.")


@models_app.command("remove")
def models_remove(id: str = typer.Argument(...)) -> None:
    """Remove a model."""
    from .config_file import load, save
    cfg = load()
    before = len(cfg.models)
    cfg.models = [m for m in cfg.models if m.id != id]
    if len(cfg.models) == before:
        typer.echo(f"Model '{id}' not found.")
        raise typer.Exit(1)
    save(cfg)
    typer.echo(f"Model '{id}' removed.")


@models_app.command("set-default")
def models_set_default(id: str = typer.Argument(...)) -> None:
    """Set the default model."""
    from .config_file import load, save
    cfg = load()
    ids = [m.id for m in cfg.models]
    if id not in ids:
        typer.echo(f"Model '{id}' not found. Available: {ids}")
        raise typer.Exit(1)
    cfg.agent.default_model = id
    save(cfg)
    typer.echo(f"Default model set to '{id}'.")


# ── routing ────────────────────────────────────────────────────────────────────

@routing_app.command("set")
def routing_set(mode: str = typer.Argument(..., help="fixed or auto")) -> None:
    """Set routing mode."""
    from .config_file import load, save
    if mode not in ("fixed", "auto"):
        typer.echo("Mode must be 'fixed' or 'auto'.")
        raise typer.Exit(1)
    cfg = load()
    cfg.agent.routing_mode = mode
    save(cfg)
    typer.echo(f"Routing mode set to '{mode}'.")


# ── skills ─────────────────────────────────────────────────────────────────────

@skills_app.command("list")
def skills_list() -> None:
    """List skills."""
    from .config import SKILLS_DIR
    from .skills.registry import SkillRegistry
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
    from .config import SKILLS_DIR
    from .skills.registry import SkillRegistry
    reg = SkillRegistry(SKILLS_DIR)
    try:
        s = reg.get(name)
    except KeyError:
        typer.echo(f"Skill '{name}' not found.")
        raise typer.Exit(1)
    typer.echo(s.body)


@skills_app.command("remove")
def skills_remove(name: str = typer.Argument(...)) -> None:
    """Remove a skill."""
    from .config import SKILLS_DIR
    from .skills.registry import SkillRegistry
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


# ── sessions ───────────────────────────────────────────────────────────────────

@sessions_app.command("list")
def sessions_list() -> None:
    """List all sessions."""
    from .server.session_store import SessionStore
    from rich.table import Table
    from rich.console import Console
    import datetime
    store = SessionStore()
    summaries = store.list(limit=50)
    table = Table(title="Sessions")
    table.add_column("ID", style="dim")
    table.add_column("Title")
    table.add_column("Messages", justify="right")
    table.add_column("Updated")
    for s in summaries:
        updated = datetime.datetime.fromtimestamp(s.updated_at).strftime("%Y-%m-%d %H:%M")
        table.add_row(s.id[:12], s.title, str(s.message_count), updated)
    Console().print(table)


@sessions_app.command("show")
def sessions_show(session_id: str = typer.Argument(...)) -> None:
    """Show messages in a session."""
    from .server.session_store import SessionStore
    from rich.table import Table
    from rich.console import Console
    store = SessionStore()
    session = store.get(session_id)
    if session is None:
        typer.echo(f"Session '{session_id}' not found.")
        raise typer.Exit(1)
    typer.echo(f"Session: {session.id}  Title: {session.title}")
    table = Table(title=f"Messages ({len(session.history)})")
    table.add_column("#", justify="right", style="dim")
    table.add_column("Role")
    table.add_column("Content")
    for i, m in enumerate(session.history):
        content = (m.content or "")[:80]
        table.add_row(str(i), m.role, content)
    Console().print(table)


# ── vault ───────────────────────────────────────────────────────────────────────

@vault_app.command("ls")
def vault_ls(path: Optional[str] = typer.Argument(None)) -> None:
    """List vault files."""
    from .vault import list_tree
    from rich.table import Table
    from rich.console import Console
    entries = list_tree()
    if path:
        entries = [e for e in entries if e.path.startswith(path.rstrip("/") + "/") or e.path == path]
    table = Table(title="Vault")
    table.add_column("Type")
    table.add_column("Path")
    table.add_column("Size", justify="right")
    for e in entries:
        size_str = str(e.size) if e.size is not None else ""
        table.add_row(e.type, e.path, size_str)
    Console().print(table)


# ── kanban ──────────────────────────────────────────────────────────────────────

@kanban_app.command("boards")
def kanban_boards() -> None:
    """List all kanban boards."""
    from .kanban import list_boards
    from rich.table import Table
    from rich.console import Console
    boards = list_boards()
    table = Table(title="Kanban Boards")
    table.add_column("Name")
    table.add_column("Cards", justify="right")
    for b in boards:
        table.add_row(b["name"], str(b["card_count"]))
    Console().print(table)


@kanban_app.command("list")
def kanban_list(
    board: str = typer.Option("default", "--board", help="Board name"),
) -> None:
    """List kanban cards."""
    from .kanban import list_cards, list_columns
    from rich.table import Table
    from rich.console import Console
    cards = list_cards(board)
    columns = list_columns(board)  # noqa: F841 — kept for future use
    table = Table(title=f"Kanban ({board})")
    table.add_column("ID", style="dim")
    table.add_column("Column")
    table.add_column("Title")
    table.add_column("Tags")
    for card in cards:
        table.add_row(card.id[:8], card.column, card.title, ",".join(card.tags))
    Console().print(table)


if __name__ == "__main__":
    app()
