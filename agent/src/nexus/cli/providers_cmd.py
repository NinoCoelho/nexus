"""Nexus CLI — providers subcommand group."""

from __future__ import annotations

import typer

providers_app = typer.Typer(help="Provider commands", no_args_is_help=True)


@providers_app.command("list")
def providers_list() -> None:
    """List providers."""
    import os
    from ..config_file import load
    from .. import secrets as _secrets
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
    from ..config_file import load, save, ProviderConfig
    cfg = load()
    cfg.providers[name] = ProviderConfig(base_url=base_url, api_key_env=key_env)
    save(cfg)
    typer.echo(f"Provider '{name}' added.")


@providers_app.command("update")
def providers_update(
    name: str = typer.Argument(...),
    base_url: str = typer.Option(None, "--base-url"),
    key_env: str = typer.Option(None, "--key-env"),
    type: str = typer.Option(None, "--type", help="openai_compat|anthropic|ollama"),
) -> None:
    """Update an existing provider entry."""
    from ..config_file import load, save

    cfg = load()
    if name not in cfg.providers:
        typer.echo(f"Provider '{name}' not found.")
        raise typer.Exit(1)
    p = cfg.providers[name]
    changed: list[str] = []
    if base_url is not None:
        p.base_url = base_url
        changed.append("base_url")
    if key_env is not None:
        p.api_key_env = key_env
        changed.append("api_key_env")
    if type is not None:
        if type not in ("openai_compat", "anthropic", "ollama"):
            raise typer.BadParameter("type must be openai_compat|anthropic|ollama")
        p.type = type
        changed.append("type")
    if not changed:
        typer.echo("Nothing to update.")
        return
    save(cfg)
    typer.echo(f"Provider '{name}' updated: {', '.join(changed)}")


@providers_app.command("set-key")
def providers_set_key(name: str = typer.Argument(...)) -> None:
    """Set an inline API key for a provider (stored in ~/.nexus/secrets.toml)."""
    import getpass
    from ..config_file import load, save
    from .. import secrets as _secrets
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
    from ..config_file import load, save
    from .. import secrets as _secrets
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
    from ..config_file import load
    from .. import secrets as _secrets
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
    from ..config_file import load, save
    cfg = load()
    if name not in cfg.providers:
        typer.echo(f"Provider '{name}' not found.")
        raise typer.Exit(1)
    del cfg.providers[name]
    save(cfg)
    typer.echo(f"Provider '{name}' removed.")
