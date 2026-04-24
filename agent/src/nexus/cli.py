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
daemon_app = typer.Typer(help="Daemon management commands", no_args_is_help=True)
trajectories_app = typer.Typer(help="Trajectory commands", no_args_is_help=True)
graphrag_app = typer.Typer(help="GraphRAG commands", no_args_is_help=True)

app.add_typer(config_app, name="config")
app.add_typer(providers_app, name="providers")
app.add_typer(models_app, name="models")
app.add_typer(routing_app, name="routing")
app.add_typer(skills_app, name="skills")
app.add_typer(sessions_app, name="sessions")
app.add_typer(vault_app, name="vault")
app.add_typer(daemon_app, name="daemon")
app.add_typer(trajectories_app, name="trajectories")
app.add_typer(graphrag_app, name="graphrag")


@app.callback()
def _global_setup() -> None:
    """Runs before every CLI subcommand.

    Installs the redacting log formatter so subcommands that print provider
    errors, config dumps, etc. mask secrets automatically. Idempotent and
    respects NEXUS_REDACT_SECRETS=false.
    """
    from .redact import install_redaction
    install_redaction(extra_loggers=("httpx",))


# ── serve ──────────────────────────────────────────────────────────────────────

@app.command()
def serve(
    port: int = typer.Option(18989, "--port", "-p"),
    host: str = typer.Option("127.0.0.1", "--host"),
    frontend_port: int = typer.Option(1890, "--frontend-port", "-fp"),
    no_frontend: bool = typer.Option(False, "--no-frontend", help="Skip launching the frontend dev server"),
    bundled: bool = typer.Option(False, "--bundled", help="Serve the built UI from the backend on a single port (skips Vite). Run `npm run build` first."),
) -> None:
    """Start the Nexus server (backend + frontend)."""
    import shutil
    import signal
    import subprocess
    import threading
    import uvicorn

    from .config import get_frontend_dir

    # --bundled implies the backend serves ui/dist itself; don't spawn Vite.
    if bundled:
        no_frontend = True
        fe = get_frontend_dir()
        dist = (fe / "dist") if fe is not None else None
        if dist is None or not (dist / "index.html").is_file():
            typer.echo(
                "No built UI found. Run `npm run build` in ui/ first, "
                "or set NEXUS_UI_DIST to a dist directory.",
                err=True,
            )
            raise typer.Exit(code=1)
        typer.echo(f"Serving bundled UI from {dist} at http://{host}:{port}")

    frontend_dir = None if no_frontend else get_frontend_dir()
    frontend_proc: list[subprocess.Popen] = []
    shutting_down = False

    if not no_frontend and frontend_dir is None:
        # Explicit diagnostic — the previous silent fallback made users
        # think the UI was broken when the installed tool just can't see
        # the repo's ui/ folder. Point them at the env var override.
        typer.echo(
            "Frontend dir not found — UI not started.\n"
            "Set NEXUS_FRONTEND_DIR to your checkout's ui/ path, e.g.:\n"
            "  NEXUS_FRONTEND_DIR=~/Code/nexus/ui nexus serve\n"
            "Or pass --no-frontend to suppress this warning.",
            err=True,
        )

    if frontend_dir is not None:
        npm = shutil.which("npm")
        if npm is None:
            typer.echo("Warning: npm not found — skipping frontend.", err=True)
        else:
            typer.echo(f"Installing frontend deps in {frontend_dir} …")
            subprocess.run([npm, "install"], cwd=str(frontend_dir), check=True, capture_output=True)
            typer.echo(f"Starting frontend dev server on http://localhost:{frontend_port}")
            fp = subprocess.Popen(
                [npm, "run", "dev", "--", "--port", str(frontend_port)],
                cwd=str(frontend_dir),
            )
            frontend_proc.append(fp)

    backend_done = threading.Event()

    def run_backend() -> None:
        uvicorn.run("nexus.main:app", host=host, port=port, reload=False)
        backend_done.set()

    backend_thread = threading.Thread(target=run_backend, daemon=True)
    backend_thread.start()

    def _shutdown(*_args: object) -> None:
        nonlocal shutting_down
        if shutting_down:
            return
        shutting_down = True
        if frontend_proc:
            frontend_proc[0].terminate()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        backend_thread.join()
    except KeyboardInterrupt:
        pass
    finally:
        if frontend_proc:
            frontend_proc[0].terminate()
            try:
                frontend_proc[0].wait(timeout=5)
            except subprocess.TimeoutExpired:
                frontend_proc[0].kill()
        typer.echo("Nexus stopped.")


# ── insights ───────────────────────────────────────────────────────────────────

@app.command()
def insights(
    days: int = typer.Option(30, "--days", "-d", help="Look-back window in days."),
    json_out: bool = typer.Option(False, "--json", help="Emit raw JSON instead of the terminal report."),
) -> None:
    """Analyze your session history — sessions, tools, activity patterns."""
    from .insights import InsightsEngine, format_terminal
    from .server.session_store import _DB_PATH

    engine = InsightsEngine(_DB_PATH)
    report = engine.generate(days=max(1, min(int(days), 365)))

    if json_out:
        import json as _json
        typer.echo(_json.dumps(report, indent=2, default=str))
        return

    typer.echo(format_terminal(report))


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
    table.add_column("Tier")
    table.add_column("Notes")
    for m in cfg.models:
        table.add_row(m.id, m.provider, m.model_name, ",".join(m.tags), m.tier, m.notes or "")
    Console().print(table)


@models_app.command("add")
def models_add(
    id: str = typer.Argument(...),
    provider: str = typer.Option(..., "--provider"),
    model_name: str = typer.Option(..., "--model"),
    tags: str = typer.Option("", "--tags"),
    tier: str = typer.Option("", "--tier", help="fast|balanced|heavy (auto-detected if omitted)"),
    notes: str = typer.Option("", "--notes"),
) -> None:
    """Add a model."""
    from .config_file import load, save, ModelEntry
    from .agent.model_profiles import suggest_tier
    cfg = load()
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    resolved_tier = tier.strip() or suggest_tier(model_name)
    if resolved_tier not in ("fast", "balanced", "heavy"):
        raise typer.BadParameter("tier must be fast|balanced|heavy")
    m = ModelEntry(
        id=id,
        provider=provider,
        model_name=model_name,
        tags=tag_list,
        tier=resolved_tier,  # type: ignore[arg-type]
        notes=notes,
    )
    cfg.models.append(m)
    save(cfg)
    typer.echo(f"Model '{id}' added (tier={resolved_tier}).")


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


@sessions_app.command("export")
def sessions_export(
    session_id: str = typer.Argument(...),
    out: Optional[str] = typer.Option(None, "--out", "-o", help="Output file path (default: stdout)"),
    port: int = typer.Option(18989, "--port"),
) -> None:
    """Export a session as markdown."""
    import httpx
    base = f"http://127.0.0.1:{port}"
    try:
        r = httpx.get(f"{base}/sessions/{session_id}/export", timeout=10)
        r.raise_for_status()
    except Exception as exc:
        typer.echo(f"[error] {exc}", err=True)
        raise typer.Exit(1)
    markdown = r.text
    if out:
        from pathlib import Path
        Path(out).write_text(markdown, encoding="utf-8")
        typer.echo(f"Exported to {out}")
    else:
        typer.echo(markdown)


@sessions_app.command("import")
def sessions_import(
    path: str = typer.Argument(..., help="Path to .md file"),
    port: int = typer.Option(18989, "--port"),
) -> None:
    """Import a session from a markdown file."""
    import httpx
    from pathlib import Path
    md = Path(path).read_text(encoding="utf-8")
    base = f"http://127.0.0.1:{port}"
    try:
        r = httpx.post(f"{base}/sessions/import", json={"markdown": md}, timeout=10)
        r.raise_for_status()
    except Exception as exc:
        typer.echo(f"[error] {exc}", err=True)
        raise typer.Exit(1)
    data = r.json()
    typer.echo(f"Imported session '{data['title']}' (id={data['id']}, messages={data['imported_message_count']})")


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


@vault_app.command("search")
def vault_search(
    query: str = typer.Argument(...),
    limit: int = typer.Option(20, "--limit", "-n"),
) -> None:
    """Full-text search across vault notes."""
    from .vault_search import search, is_empty, rebuild_from_disk
    from rich.table import Table
    from rich.console import Console
    from rich.text import Text

    if is_empty():
        typer.echo("Index empty — rebuilding…")
        n = rebuild_from_disk()
        typer.echo(f"Indexed {n} files.")

    results = search(query, limit=limit)
    if not results:
        typer.echo("No results.")
        return

    table = Table(title=f'Search: "{query}"')
    table.add_column("Path", style="cyan", no_wrap=True)
    table.add_column("Snippet")
    for r in results:
        snippet = r["snippet"].replace("<mark>", "[bold yellow]").replace("</mark>", "[/bold yellow]")
        table.add_row(r["path"], Text.from_markup(snippet))
    Console().print(table)


@vault_app.command("reindex")
def vault_reindex() -> None:
    """Rebuild the full-text search index from disk."""
    from .vault_search import rebuild_from_disk
    n = rebuild_from_disk()
    typer.echo(f"Indexed {n} files.")


@vault_app.command("tags")
def vault_tags() -> None:
    """List all tags in the vault with file counts."""
    from .vault_index import list_tags, is_empty, rebuild_from_disk as rebuild_meta
    from rich.table import Table
    from rich.console import Console

    if is_empty():
        typer.echo("Tag index empty — rebuilding…")
        n = rebuild_meta()
        typer.echo(f"Indexed {n} files.")

    tags = list_tags()
    if not tags:
        typer.echo("No tags found.")
        return

    table = Table(title="Vault Tags")
    table.add_column("Tag", style="cyan")
    table.add_column("Files", justify="right")
    for t in tags:
        table.add_row(t["tag"], str(t["count"]))
    Console().print(table)


@vault_app.command("backlinks")
def vault_backlinks_cmd(path: str = typer.Argument(..., help="Relative vault file path")) -> None:
    """List files that link to a given vault file."""
    from .vault_index import backlinks, is_empty, rebuild_from_disk as rebuild_meta
    from rich.console import Console

    if is_empty():
        typer.echo("Tag index empty — rebuilding…")
        rebuild_meta()

    links = backlinks(path)
    if not links:
        typer.echo(f"No backlinks to '{path}'.")
        return

    console = Console()
    console.print(f"[bold]Backlinks to[/bold] [cyan]{path}[/cyan]:")
    for link in links:
        console.print(f"  [dim]-[/dim] {link}")


# ── daemon ───────────────────────────────────────────────────────────────────────

@daemon_app.command("start")
def daemon_start(
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind to"),
    port: int = typer.Option(18989, "--port", "-p", help="Port to bind to"),
    detach: bool = typer.Option(True, "--detach/--no-detach", help="Run as daemon or in foreground"),
    no_frontend: bool = typer.Option(False, "--no-frontend", help="Skip launching the frontend dev server"),
) -> None:
    """Start the Nexus daemon."""
    from .daemon import daemon_manager
    if no_frontend:
        from .daemon import DaemonManager
        original = DaemonManager._start_frontend
        DaemonManager._start_frontend = lambda self: None
        try:
            daemon_manager.start(host=host, port=port, detach=detach)
        finally:
            DaemonManager._start_frontend = original
    else:
        daemon_manager.start(host=host, port=port, detach=detach)


@daemon_app.command("stop")
def daemon_stop() -> None:
    """Stop the Nexus daemon."""
    from .daemon import daemon_manager
    daemon_manager.stop()


@daemon_app.command("restart")
def daemon_restart(
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind to"),
    port: int = typer.Option(18989, "--port", "-p", help="Port to bind to"),
) -> None:
    """Restart the Nexus daemon."""
    from .daemon import daemon_manager
    daemon_manager.restart(host=host, port=port)


@daemon_app.command("status")
def daemon_status() -> None:
    """Show daemon status."""
    from .daemon import daemon_manager
    daemon_manager.show_status()


@daemon_app.command("install")
def daemon_install(
    user: bool = typer.Option(True, "--user/--system", help="Install as user or system service"),
) -> None:
    """Install Nexus as a system service."""
    from .daemon import service_installer
    service_installer.install_service(user=user)


@daemon_app.command("uninstall")
def daemon_uninstall(
    user: bool = typer.Option(True, "--user/--system", help="Uninstall user or system service"),
) -> None:
    """Uninstall Nexus system service."""
    from .daemon import service_installer
    service_installer.uninstall_service(user=user)


@daemon_app.command("logs")
def daemon_logs(
    lines: int = typer.Option(50, "--lines", "-n", help="Number of lines to show"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow log output (press 'q' to quit)"),
) -> None:
    """Show daemon logs."""
    from .daemon import daemon_manager
    daemon_manager.show_logs(lines=lines, follow=follow)


# ── trajectories ──────────────────────────────────────────────────────────────────

@trajectories_app.command("export")
def trajectories_export(
    output: Optional[str] = typer.Option(
        None, "--output", "-o", help="Output file path (default: trajectories-export.jsonl in cwd)"
    ),
    since: Optional[str] = typer.Option(
        None, "--since", help="Include only records from this date onwards (YYYY-MM-DD)"
    ),
) -> None:
    """Export trajectory records to a JSONL file."""
    from pathlib import Path
    from .trajectory import TrajectoryLogger

    out_path = Path(output) if output else Path("trajectories-export.jsonl")
    logger = TrajectoryLogger()
    try:
        count = logger.export(out_path, since_date=since)
    except Exception as exc:
        typer.echo(f"[error] {exc}", err=True)
        raise typer.Exit(1)
    typer.echo(f"Exported {count} records to {out_path}")


# ── graphrag ───────────────────────────────────────────────────────────────────

@graphrag_app.command("reindex")
def graphrag_reindex() -> None:
    """Drop GraphRAG data and rebuild the index from vault files."""
    import asyncio
    from .agent.graphrag_manager import drop_data, initialize, index_full_vault
    from .config_file import load as load_config

    cfg = load_config()
    if not cfg.graphrag.enabled:
        typer.echo("GraphRAG is not enabled in config.", err=True)
        raise typer.Exit(1)

    dropped = drop_data()
    typer.echo(f"Dropped {dropped} GraphRAG database file(s).")

    async def _run() -> None:
        await initialize(cfg)
        typer.echo("Engine initialized. Indexing vault…")
        await index_full_vault()

    asyncio.run(_run())
    typer.echo("GraphRAG reindex complete.")


if __name__ == "__main__":
    app()
