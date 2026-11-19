"""External-agent delegation handoff page.

``GET /ext/delegate`` — self-contained page (no UI build, same pattern as
``GET /ext``) with the Nexus delegation skill in a copyable block. The
user pastes it into any external coding agent (Claude Code, OpenCode,
Codex); the agent follows the embedded self-install steps, saves the
skill in its own format, verifies ``nexus bots list`` works, and
confirms. Includes per-agent install hints and a live CLI checklist.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from ...ext_skill import SKILL_NAME, delegation_skill_markdown

router = APIRouter()


def _server_port() -> int:
    try:
        from ..config import PORT

        return int(PORT)
    except Exception:
        return 18989


@router.get("/ext/delegate", response_class=HTMLResponse)
async def ext_delegate_page() -> HTMLResponse:
    port = _server_port()
    skill = delegation_skill_markdown(port=port)
    # Escape for safe embedding in the <pre> block.
    skill_html = (
        skill.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="nexus-server" content="1">
<title>Nexus — delegate to your coding agent</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 760px;
         margin: 48px auto; padding: 0 20px; color: #e6e8ec; background: #16181d; }}
  h1 {{ font-size: 22px; }} h2 {{ font-size: 16px; margin-top: 32px; }}
  p, li {{ line-height: 1.55; font-size: 14px; color: #c3c9d4; }}
  code {{ background: #1d2026; border: 1px solid #2c303a; border-radius: 4px;
          padding: 2px 8px; font-family: ui-monospace, Menlo, monospace; font-size: 13px; }}
  button {{ background: #1d2026; color: #9aa3b2; border: 1px solid #2c303a; border-radius: 6px;
            padding: 6px 14px; cursor: pointer; font-size: 13px; }}
  button:hover {{ border-color: #5b8cff; color: #e6e8ec; }}
  button.primary {{ background: #2c4a8f; border-color: #2c4a8f; color: #e6e8ec; }}
  button.primary:hover {{ background: #3557a8; }}
  pre {{ background: #101216; border: 1px solid #2c303a; border-radius: 8px;
         padding: 16px; font-family: ui-monospace, Menlo, monospace; font-size: 12px;
         line-height: 1.5; overflow: auto; max-height: 420px; white-space: pre-wrap; }}
  .row {{ display: flex; align-items: center; gap: 12px; margin: 8px 0; }}
  .hint {{ color: #9aa3b2; font-size: 13px; }}
  .check {{ color: #7ad0a0; }}
</style>
</head>
<body>
<h1>Delegate Nexus work to your coding agent</h1>
<p>Give Claude Code, OpenCode, or Codex the ability to hand tasks to your
Nexus bots. Two ways — pick one:</p>
<p><strong>Option A (simplest):</strong> tell your agent one sentence —
<code>curl http://127.0.0.1:{port}/bootstrap and follow it</code>. The
agent reads the living setup doc (always current, lists your real bots)
and installs what it needs.</p>
<p><strong>Option B (offline copy):</strong> paste the block below into
the agent; it <strong>configures itself</strong> from the embedded text.</p>

<div class="row">
  <code>curl http://127.0.0.1:{port}/bootstrap</code>
  <button data-copy="curl http://127.0.0.1:{port}/bootstrap">copy curl</button>
  <button class="primary" id="copyAll">Copy skill block</button>
  <span class="hint" id="copyNote">paste into your agent; it self-installs</span>
</div>
<pre id="skill">{skill_html}</pre>

<h2>Per-agent notes</h2>
<ul>
  <li><strong>Claude Code</strong> — the agent saves it as a personal skill
      (<code>~/.claude/skills/{SKILL_NAME}/SKILL.md</code>); available on the next session.</li>
  <li><strong>OpenCode</strong> — appended to <code>~/.opencode/AGENTS.md</code>
      (or its skills folder, when supported).</li>
  <li><strong>Codex CLI</strong> — appended to <code>~/.codex/AGENTS.md</code>.</li>
</ul>

<h2>Verify afterwards</h2>
<p>From the agent's terminal (or yours):</p>
<div class="row">
  <code>nexus bots list --json</code>
  <button data-copy="nexus bots list --json">copy</button>
</div>
<p class="hint">If this lists your bots, the agent can delegate.
Requires the daemon: <code>nexus daemon start</code>.</p>

<h2>Try it</h2>
<div class="row">
  <code>nexus run @handle "summarize this repo" --json</code>
  <button data-copy="nexus run @handle &quot;summarize this repo&quot; --json">copy</button>
  <span class="hint">replace <code>@handle</code> with a real bot handle</span>
</div>

<p class="hint check" id="port">Server on port {port} — the CLI reads
NEXUS_PORT/default 18989 automatically.</p>

<script>
  const skillText = document.getElementById("skill").textContent;
  document.getElementById("copyAll").onclick = async () => {{
    try {{
      await navigator.clipboard.writeText(skillText);
      const n = document.getElementById("copyNote");
      n.textContent = "copied ✓ — paste into your agent and it self-installs";
      setTimeout(() => {{ n.textContent = "paste it into your agent, then ask it to install"; }}, 4000);
    }} catch (e) {{
      const n = document.getElementById("copyNote");
      n.textContent = "copy failed — select the block manually";
    }}
  }};
  document.querySelectorAll("button[data-copy]").forEach((b) => {{
    b.onclick = () => navigator.clipboard.writeText(b.dataset.copy);
  }});
</script>
</body>
</html>"""
    return HTMLResponse(html)
