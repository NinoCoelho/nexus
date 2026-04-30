# Skill format

A skill is a directory containing a `SKILL.md` file. The agent loads
the description into its system prompt; the body is fetched on demand
when the agent picks up the skill.

```
my-skill/
├── SKILL.md          # required — frontmatter + body
└── (any extra files) # optional — read by the skill via tools
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
skills (`brainstorm` etc.) get re-seeded on next start.
