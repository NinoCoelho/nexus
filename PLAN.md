# Nexus — Architecture & MVP Plan

## Vision
Self-evolving agentic platform. Proprietary agent core (Hermes-style markdown skills, self-authored at runtime) is the primary execution path. External agents are reachable over ACP as a secondary lane via an `acp_call` tool. Frontend is a fresh React app — knowspace-inspired layout, distinct palette.

## Heritage
- **ai-monitor (helyx)** — direct base. Keep: FastAPI server shape, skill registry + agentskills.io format, LLM provider port, session/context pattern, internal-state tools, Cytoscape canvas.
- **hermes-agent (NousResearch, MIT)** — pattern source for self-evolution. Port: `skill_manage` (create/edit/patch/delete/write_file/remove_file), `skills_guard` (regex static scan + trust tiers), `SKILLS_GUIDANCE` prompt, progressive-disclosure system prompt builder.
- **knowspace** — pattern source for ACP bridge. Port: `lib/gateway.js` shape (WS + ed25519 device auth) as a Python `acp_client.py`.

## Core architecture
```
nexus/
  agent/                          # Python + FastAPI backend
    src/nexus/
      agent/
        loop.py                   # tool-calling loop (fork of helyx/agent/loop.py)
        prompt_builder.py         # progressive-disclosure sys prompt + SKILLS_GUIDANCE
        llm.py                    # provider port (OpenAI-compat + Anthropic native)
      skills/
        registry.py               # agentskills.io markdown loader (fork of helyx)
        manager.py                # skill_manage tool (port of hermes skill_manager_tool.py)
        guard.py                  # regex security scan (port of hermes skills_guard.py)
        types.py
      tools/
        acp_call.py               # external-agent bridge (stub for MVP)
        http_call.py
        state_tool.py             # list_skills / list_agents (helyx pattern)
      server/
        app.py                    # FastAPI app
        session.py                # in-memory session store (helyx pattern)
        schemas.py
      config.py
      main.py
    pyproject.toml
    ~/.nexus/skills/              # runtime skill dir (home-relative, like ~/.hermes/skills)

  ui/                             # React + Vite frontend
    src/
      App.tsx
      tokens.css                  # palette tokens (copper + sage on warm slate)
      components/
        ChatView.tsx              # primary interface
        SkillsPanel.tsx           # live skill list; badges: builtin / user / agent-authored
        Sidebar.tsx               # view switcher (knowspace pattern, minus vault/kanban)
      api.ts
      main.tsx
    index.html
    package.json
    vite.config.ts

  skills/                         # bundled skills (seeded to ~/.nexus on first run)
    hello-world/SKILL.md

  README.md
  PLAN.md
```

## Palette (design decision — distinctive, not AI-cliche)
Warm dark slate + copper + sage. Avoids knowspace (brown/cream), helyx (blue/navy), and common AI-platform purple/cyan.

```
--bg:         #15181c    /* warm dark slate */
--bg-panel:   #1d2025
--bg-hover:   #262a31
--bg-inset:   #0f1114
--border:     #2c3037
--border-soft:#23272e

--fg:         #ece8e1    /* warm off-white */
--fg-dim:     #a39d92
--fg-faint:   #6b6660

--accent:     #d4855c    /* copper — primary brand */
--accent-hi:  #e09a75
--accent-lo:  #b06e4a

--sage:       #8ba888    /* secondary */
--amber:      #e0b364
--rust:       #c85a5a

--ok:         #8ba888
--warn:       #e0b364
--bad:        #c85a5a
```

Font stack: `"Geist", "Inter", ui-sans-serif, system-ui` for UI; `"JetBrains Mono", ui-monospace` for code.

## MVP scope (vertical slice → ready-for-testing)
1. Chat loop that calls OpenAI-compat or Anthropic models, with tool-calling.
2. Skill registry reading `~/.nexus/skills/**/SKILL.md`.
3. `skill_manage` tool: agent can create/edit/patch/delete skills at runtime; `skills_guard` blocks dangerous agent-authored content.
4. Progressive disclosure: system prompt lists only `name + description`; agent calls `skill_view` to read body.
5. Session store with per-session context (helyx pattern).
6. React UI with chat + live skills panel. Palette applied.
7. `acp_call` tool stub (returns "ACP bridge not configured" until user wires knowspace gateway).
8. README with setup + run + first-chat flow.

## Deferred (M2+)
- Multi-agent routing / planner-executor split
- Persistent sessions (SQLite + FTS)
- Memory plugins (MEMORY.md, mem0, honcho)
- Cytoscape canvas view (nodes for agents/skills/targets)
- Knowspace-style auth / device identity
- Atropos RL trajectory logging

## Build strategy
- Backend and frontend are disjoint directories, so they build in parallel via two implementer agents.
- LLM provider port defaults to OpenAI-compat (env: `NEXUS_LLM_BASE_URL`, `NEXUS_LLM_API_KEY`, `NEXUS_LLM_MODEL`). Anthropic adapter is a drop-in for later.
- `skills_guard` is a speed bump, not a boundary (documented in code comments).
