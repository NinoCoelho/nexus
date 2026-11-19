"""Nexus CLI — headless run: drive a bot from scripts and CI.

``nexus run <bot> "<task>"`` sends one message to a bot's chat over
loopback HTTP (same pattern as tunnel_cmd/pair_cmd) and prints the final
reply. ``--json`` emits a single JSON document (session id, reply, usage
telemetry) so CI can parse it; exit codes branch on failure:

- 0 — turn completed, reply printed
- 1 — turn failed (HTTP error, budget exceeded, daemon absent)
- 2 — bot not found / usage error
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

import typer

run_app = typer.Typer(help="Headless bot runs for scripts and CI")

bots_app = typer.Typer(help="Inspect bots from the terminal", no_args_is_help=True)


def _api_base() -> str:
    import os

    port = os.environ.get("NEXUS_PORT", "18989")
    return f"http://127.0.0.1:{port}"


def _request(method: str, path: str, body: dict | None = None, timeout: int = 120) -> dict:
    url = f"{_api_base()}{path}"
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url=url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read().decode("utf-8")).get("detail", "")
        except Exception:
            pass
        typer.echo(f"error: {e.code} {detail}", err=True)
        raise typer.Exit(code=1)
    except urllib.error.URLError:
        typer.echo(
            "error: could not reach the Nexus server. Is the daemon running? "
            "(`uv run nexus daemon start`)",
            err=True,
        )
        raise typer.Exit(code=1)


def _resolve_bot(ref: str) -> dict:
    """Find a bot by id or @handle via GET /bots (loopback, no auth)."""
    bots = _request("GET", "/bots")
    want = ref.strip().lstrip("@").lower()
    for b in bots.get("bots", []):
        if str(b.get("id", "")).lower() == want or str(b.get("handle", "")).lower() == want:
            return b
    return {}


def run(
    bot: str = typer.Argument(..., help="Bot id or @handle to run the turn on."),
    message: str = typer.Argument(..., help="The user message / task to send."),
    json_out: bool = typer.Option(False, "--json", help="Emit one JSON document (session_id, reply, iterations)."),
    new: bool = typer.Option(False, "--new", help="Start a fresh chat instead of continuing the bot's most recent one."),
    timeout: int = typer.Option(600, "--timeout", help="Max seconds to wait for the turn (default 600)."),
    model: str = typer.Option("", "--model", help="Override the model for this turn only."),
) -> None:
    """Send one task to a bot and print its final reply.

    Example (CI):

        nexus run research "summarize https://example.com/post" --json | jq -r .reply
    """
    info = _resolve_bot(bot)
    if not info:
        typer.echo(f"error: bot not found: {bot} (see `nexus bots list`)", err=True)
        raise typer.Exit(code=2)

    project_id = info.get("id")
    session_id: str | None = None
    if not new and info.get("kind") != "coordinator":
        # Continue the bot's most recent chat when one exists.
        try:
            sess = _request("GET", f"/projects/{project_id}")
            sessions = (sess.get("sessions") or []) if isinstance(sess, dict) else []
            if sessions:
                session_id = sessions[0].get("id")
        except typer.Exit:
            session_id = None

    body: dict = {
        "message": message,
        "project_id": project_id,
        "input_mode": "text",
    }
    if session_id:
        body["session_id"] = session_id
    if model:
        body["model"] = model

    try:
        reply = _request("POST", "/chat", body, timeout=timeout)
    except typer.Exit:
        # _request already printed the error; map HTTP failure to exit 1.
        raise typer.Exit(code=1)

    if json_out:
        doc = {
            "session_id": reply.get("session_id", ""),
            "reply": reply.get("reply", ""),
            "iterations": reply.get("iterations", 0),
            "skills_touched": reply.get("skills_touched", []),
            "bot": {"id": info.get("id"), "handle": info.get("handle"), "name": info.get("name")},
        }
        typer.echo(json.dumps(doc, ensure_ascii=False, indent=2))
    else:
        typer.echo(reply.get("reply", ""))


@bots_app.command("list")
def bots_list(
    json_out: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """List every bot (id, @handle, name, persona preview)."""
    bots = _request("GET", "/bots")
    rows = bots.get("bots", [])
    if json_out:
        typer.echo(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    if not rows:
        typer.echo("No bots configured.")
        return
    for b in rows:
        kind = b.get("kind", "bot")
        handle = b.get("handle", "")
        name = b.get("name", "")
        desc = (b.get("description") or "")[:60]
        typer.echo(f"{b.get('id', '')[:12]:<14} @{handle:<20} {name:<24} [{kind}] {desc}")
