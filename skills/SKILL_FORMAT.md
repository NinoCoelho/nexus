# Skill format

A skill is a directory containing a `SKILL.md` file. The agent loads
the description into its system prompt; the body is fetched on demand
when the agent picks up the skill.

```
my-skill/
├── SKILL.md              # required — frontmatter + body
├── requirements.txt      # optional — Python deps (isolated venv auto-created)
└── (any extra files)     # optional — read by the skill via tools
```

## Frontmatter (required)

```yaml
---
name: my-skill                 # ^[a-z][a-z0-9-]{0,63}$
description: One sentence — when to use this skill, with concrete trigger phrases.
type: procedure                 # informational; not enforced
role: research                  # informational; not enforced
platform: nexus
platform_version: "0.1"
nexus_status: stable
nexus_authored_by: builtin      # builtin | user | agent
---
```

`name` and `description` are required. The description is what the
router sees — make it concrete and short.

## Body

Markdown. Write it as instructions for the agent, not for a human
reader. A useful skeleton:

```markdown
# my-skill

Use when ... Skip when ...

## Procedure

1. ...
2. ...

## Constraints

- ...
```

## Installing a skill

Local path:

```bash
nexus skills install ./path/to/my-skill
```

Git URL (cloned with `--depth 1`):

```bash
nexus skills install https://github.com/you/your-skill.git
nexus skills install https://github.com/you/repo.git --subdir skills/foo
```

The installer runs the same regex guard as agent-authored skills
before enabling. If the verdict is `caution` or `dangerous`, you'll
be asked to confirm — pass `--yes` to skip the prompt in scripts.
Pass `--force` to overwrite an existing skill of the same name, and
`--name <other>` to rename on install.

## Removing a skill

```bash
nexus skills remove my-skill
```

This deletes the directory under `~/.nexus/skills/<name>`. Bundled
skills (`brainstorm` etc.) are tracked in
`~/.nexus/skills/.seeded-builtins.json`. Re-seeding only occurs when
the skill name is **not** already in that file. To force a re-seed
(e.g. after a bundled skill update), remove the name from the
`seeded` array in `.seeded-builtins.json` and restart Nexus.

## External binaries / runtimes

If the skill needs CLI tools (ffmpeg, tesseract, demucs, node, etc.) or
custom Python venvs, the body **must** start with a pre-flight block
that runs `command -v <tool> >/dev/null || { echo "missing: <tool>";
exit 1; }` and includes brew/apt/pip install hints. The agent
surfaces the failure to the user without aborting the session.

## Python dependencies (managed venvs)

If a skill ships a `requirements.txt` at its root, Nexus automatically
creates an isolated virtual environment under `~/.nexus/venvs/<skill-name>/`.
The venv is created at seed/install time and re-synced whenever
`requirements.txt` changes.

```yaml
---
name: my-skill
description: ...
python_version: "3.11"        # optional — pin a specific Python version
---
```

The agent discovers the venv path via `skill_view(name="my-skill")`,
which returns a `python.path` field like
`~/.nexus/venvs/my-skill/bin/python3`. The agent uses this path to
run the skill's scripts instead of bare `python3`.

**Portability:** The skill folder (under `~/.nexus/skills/`) stays
self-contained — only `SKILL.md`, `requirements.txt`, and `scripts/`
are migrated. The venv is a derived artifact recreated from the
manifest on the target host.

To manually trigger venv creation or re-sync:

```
skill_manage(action="ensure_venv", name="my-skill")
```
