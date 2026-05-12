---
name: project-init
description: Use when the user wants to create a new project, start a project, scaffold a project workspace, or set up a project folder. Creates a vault folder with documentation structure and a kanban board. Do not use for adding files to an existing project — use vault_write directly for that.
type: procedure
role: project-management
platform: nexus
platform_version: "0.1"
nexus_status: stable
nexus_authored_by: builtin
---

# project-init

Use when the user wants a new project workspace. Creates a standardized folder structure in the vault with a kanban board, documentation skeleton, and project overview. Skip for one-off files, notes, or ad-hoc tasks that don't warrant a full project structure.

## Procedure

### 1. Gather project info

Ask the user (only if not already provided):

- **Project name** — used as display title and to derive the slug.
- **Brief description** — 1-2 sentences for the overview file.
- **Tech stack** (optional) — languages, frameworks, key dependencies.
- **Code location** — one of:
  - **External** (default): absolute path to an existing repo, or a URL to clone. Code lives outside the vault; the vault holds docs + kanban only.
  - **Vault-internal**: code lives inside `projects/<slug>/src/`. Agent works on code via `vault_read`/`vault_write` + `terminal`.
  - **None**: documentation-only project, no code.

Derive the slug: project name → lowercase, spaces and special chars to hyphens, max 50 chars.

### 2. Check for conflicts

`vault_list path=projects` and verify `projects/<slug>/` does not already exist. If it does:

- Offer to link to the existing project instead.
- If the user insists on recreating, ask for confirmation and note that existing files will be overwritten.

### 3. Create folder structure

Create the following files via `vault_write`:

```
projects/<slug>/overview.md
projects/<slug>/kanban.md
projects/<slug>/changelog.md
projects/<slug>/specs/.gitkeep
projects/<slug>/research/.gitkeep
projects/<slug>/decisions/.gitkeep
```

### 4. Write overview.md

```markdown
---
tags: [project, <slug>]
status: active
code_location: external | vault | none
code_path: <path-or-vault-relative>
created: YYYY-MM-DD
---

# <Project Name>

<Description>

## Tech Stack

- <stack items, or "Not yet defined">

## Status

Active

## Links

- [Kanban Board](vault://projects/<slug>/kanban.md)
- [Changelog](vault://projects/<slug>/changelog.md)
```

Keep initial content under 500 chars. The user and agent will expand it as the project evolves.

### 5. Write kanban.md

Create a 6-lane board with auto-dispatch prompts configured in lane frontmatter.

Use `kanban_manage action=create_board` with:

**Board title:** `<Project Name>`

**Lanes (in order):**

| Lane | Purpose | Auto-dispatch prompt |
|---|---|---|
| Ideas | Parking lot, unstructured | None |
| Research | Investigation phase | `"Use the deep-research skill to investigate this topic. Write findings to projects/<slug>/research/<descriptive-slug>.md. Update the card body with a summary and vault:// link to the research file."` |
| Planned | Spec'd and ready to implement | None |
| In Progress | Active implementation | `"Use the coding-style skill to implement this card. Follow TDD discipline. Update card status to running. Commit following git-workflow conventions."` |
| Testing | Review and verification | `"Use the code-review-local skill to review and verify this card's implementation. Run all relevant tests. Flag regressions or issues. Update card status accordingly."` |
| Done | Completed | None |

**Lane prompts go in the board frontmatter:**

```yaml
lane_prompts:
  research: "Use the deep-research skill to investigate this topic. Write findings to projects/<slug>/research/<descriptive-slug>.md. Update the card body with a summary and vault:// link to the research file."
  in-progress: "Use the coding-style skill to implement this card. Follow TDD discipline. Update card status to running. Commit following git-workflow conventions."
  testing: "Use the code-review-local skill to review and verify this card's implementation. Run all relevant tests. Flag regressions or issues. Update card status accordingly."
```

### 6. Write changelog.md

```markdown
---
tags: [changelog, <slug>]
---

# <Project Name> — Changelog

All notable changes tracked by card completion.
```

### 7. Git initialization (if vault-internal)

If code_location is `vault`:

1. `terminal command="git init projects/<slug>/src"` (request approval).
2. Create initial `projects/<slug>/src/.gitkeep`.
3. Initial commit.

If code_location is `external` and the path points to an existing repo, verify with `terminal command="git -C <path> status"`.

If code_location is `external` and the path is a URL, clone it via `terminal command="git clone <url> <path>"` (request approval).

### 8. Confirm and link

Return to the user:

```
Project "<name>" created at vault://projects/<slug>/overview.md

Structure:
- overview.md — project context and status
- kanban.md — 6-lane board (Ideas → Research → Planned → In Progress → Testing → Done)
- specs/ — feature specifications
- research/ — research artifacts
- decisions/ — architecture decision records
- changelog.md — change log

Code location: <external at /path | vault at projects/<slug>/src | none>

Next: add cards to the Ideas lane, or ask me to create a card from a feature description.
```

## Constraints

- Never overwrite an existing project folder without explicit confirmation.
- Never store code inside the vault for external-location projects — only docs and kanban.
- The kanban board is the single source of truth for project status. Do not duplicate status tracking elsewhere.
- Lane prompts reference other skills by name. If a referenced skill doesn't exist, create the lane without a prompt and note the missing skill.
- Keep the initial overview minimal. The project grows organically as cards are worked.

## Project folder conventions

### specs/

Feature specifications written before implementation. Each spec is a markdown file:

```markdown
---
tags: [spec, <slug>]
status: draft | approved | implemented
card_id: <card-id when linked>
---

# <Feature Title>

## Context
<Why this feature exists>

## Acceptance Criteria
- <observable, testable criteria>

## Out of Scope
- <explicit exclusions>
```

### research/

Research artifacts from the deep-research skill or ad-hoc investigation. Named descriptively: `research/auth-options.md`, `research/db-benchmark.md`.

### decisions/

Architecture Decision Records (ADRs). Format:

```markdown
---
tags: [adr, <slug>]
status: proposed | accepted | deprecated
date: YYYY-MM-DD
---

# ADR-NNNN: <Title>

## Context
<What is the issue that we're seeing that is motivating this decision>

## Decision
<What is the change that we're proposing>

## Consequences
<What becomes easier or harder because of this change>
```

Number ADRs sequentially starting from 0001.
