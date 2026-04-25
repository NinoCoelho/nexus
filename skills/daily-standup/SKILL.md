---
name: daily-standup
description: Create or update the user's daily standup note in the vault with yesterday's work, today's plan, and blockers. Use when the user says "standup", "daily", "log today", or asks to start a new day.
type: procedure
role: vault
platform: nexus
platform_version: "0.1"
nexus_status: stable
nexus_authored_by: builtin
---

# daily-standup

Use when the user wants to record or update a daily standup. Otherwise leave standalone notes alone.

## Procedure

1. Determine today's date (ISO `YYYY-MM-DD`).
2. Check if `standups/<date>.md` exists in the vault using `vault_list`. If not, create it with the template below. If it exists, read it and update only the relevant section.
3. Ask the user, in one message, three short questions if any are missing:
   - What did you ship yesterday?
   - What are you focused on today?
   - Any blockers?
4. Write or patch the file with `vault_create` or `vault_update`. Keep frontmatter intact across edits.
5. Reply with a one-line confirmation and a `vault://standups/<date>.md` link.

## Template

```markdown
---
type: standup
date: <YYYY-MM-DD>
---

# Standup · <YYYY-MM-DD>

## Yesterday
- …

## Today
- …

## Blockers
- …
```

## Constraints

- Never delete content the user wrote — append or update specific sections only.
- If the user provides multiple updates in one message, parse them into the right sections instead of dumping everything under one heading.
- Standup notes go under `standups/`. Do not put them at the vault root.
