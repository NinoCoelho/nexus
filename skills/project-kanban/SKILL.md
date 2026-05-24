---
name: project-kanban
description: Use when managing kanban boards for project work — creating cards, moving cards between lanes, querying across boards, spawning sessions from cards, or running daily standups. Works with boards created by project-init. Do not use for generic vault file operations or for creating new projects (use project-init instead).
type: procedure
role: project-management
platform: nexus
platform_version: "0.1"
nexus_status: stable
nexus_authored_by: builtin
---

# project-kanban

Use when working with project kanban boards — creating cards from specs, moving cards, tracking progress, querying across boards, and spawning agent sessions from cards. Skip for creating new projects (use `project-init`), editing non-kanban vault files (use `vault_write`), or ad-hoc tasks with no project context.

## Board location

Project boards live at `projects/<slug>/kanban.md`. Always use the project slug to locate the board.

If the user doesn't specify which project, identify it from:
- Current session context (card_id or project reference)
- User's description of the work
- `kanban_query` across all boards to find relevant cards

## Creating cards

### From a spec

When a spec exists at `projects/<slug>/specs/<name>.md`:

1. `vault_read projects/<slug>/specs/<name>.md` to get acceptance criteria and details.
2. `kanban_manage action=add_card` to the "Ideas" or "Planned" lane.
3. Card body includes:
   - Summary (2-4 sentences from spec context section)
   - Link: `vault://projects/<slug>/specs/<name>.md`
   - Acceptance criteria (bulleted)
4. Set metadata from spec:
   - `priority`: from spec frontmatter or ask the user
   - `labels`: from spec tags or project-relevant labels
   - `due`: ask if relevant

### From a user description

When the user describes work without a spec:

1. Write the card to the "Ideas" lane (user or agent can move to "Planned" after refinement).
2. Card body includes the user's description verbatim.
3. Optionally suggest creating a spec via `vault_write` if the card is complex.

### Card metadata conventions

| Field | Format | Example |
|---|---|---|
| `priority` | `low`, `med`, `high`, `urgent` | `high` |
| `labels` | comma-separated tags | `backend, auth, security` |
| `due` | ISO date `YYYY-MM-DD` | `2025-02-15` |
| `assignees` | comma-separated names | `alice, bob` |

Card metadata is stored as `<!-- nx:key=value -->` HTML comments inside the card body. Always preserve these when editing card content.

## Moving cards

### Standard move

`kanban_manage action=move_card path=projects/<slug>/kanban.md card_id=<id> dst_lane=<lane>`

Moving a card to a lane with a configured prompt triggers the lane-change hook, which auto-dispatches an agent session with that prompt.

### Lane-specific behavior

**Moving to "Research":**
- Lane-change hook fires with the deep-research prompt.
- Agent investigates the topic and writes findings to `projects/<slug>/research/`.
- Card body updated with summary and vault link to research file.
- No branch creation needed.

**Moving to "In Progress":**
- Lane-change hook fires with the coding-style prompt.
- Agent creates a branch following `git-workflow` conventions: `feat/<slug>` or `fix/<slug>`.
- Branch name recorded in card body: `<!-- nx:branch=feat/<slug> -->`.
- Card status set to `running`.
- Agent implements following TDD discipline.
- On completion: card status → `done`, card checked → `true`.

**Moving to "Testing":**
- Lane-change hook fires with the code-review-local prompt.
- Agent reviews the implementation, runs tests, flags issues.
- If issues found: move card back to "In Progress" with notes on what needs fixing.
- If clean: move card to "Done".

**Moving to "Done":**
- Card status set to `done`, checked set to `true`.
- Branch merged following `git-workflow` conventions.
- Changelog entry appended to `projects/<slug>/changelog.md`.
- If the card has a linked PR, note the merge.

### Manual override

If the user wants to move a card without triggering the auto-dispatch hook (e.g., "just reorganize the board"), note this explicitly. The lane-change hook fires regardless, but the agent can choose not to act on the prompt.

## Querying across boards

### Find cards by status

```
kanban_query status=running
```

Returns cards with `running` status across all project boards.

### Find cards by text

```
kanban_query q="authentication"
```

Full-text search across all boards.

### Find cards by label, priority, assignee

```
kanban_query labels=["backend"] priority="high" assignee="alice"
```

### Find overdue cards

```
kanban_query due_before="2025-02-01" status=running
```

### Cross-board summary

When the user asks "what's across all projects":

1. `kanban_query` with no filters to get all cards, or run targeted queries per status.
2. Group results by project/board.
3. Summarize: N projects, M cards in progress, K overdue, L blocked.

## Spawning sessions from cards

### Background execution

For autonomous card execution:

```
dispatch_card path=projects/<slug>/kanban.md card_id=<id> mode=background
```

The agent starts a server-side session with the card's lane prompt as seed. Card status flips to `running` and resolves to `done`/`failed` when the turn completes.

Use for:
- Research cards (deep investigation without user supervision)
- Review cards (automated code review)

### Interactive session

For cards that need user input during execution:

```
dispatch_card path=projects/<slug>/kanban.md card_id=<id> mode=chat
```

Returns a `seed_message` for the UI to start a new chat session. The user supervises the agent's work.

Use for:
- Implementation cards (user may need to answer clarifying questions)
- Complex features where the user wants to see progress in real-time

### Session linking

After dispatch, the session ID is auto-linked to the card via `<!-- nx:session=<sid> -->`. Subsequent queries can find cards by session ID.

## Daily standup

When the user asks for a standup, status update, or "what's happening":

1. **In Progress:** `kanban_query status=running` — cards currently being worked.
2. **Recently Done:** Check "Done" lane for cards completed recently (today/yesterday).
3. **Blocked:** Look for cards in "In Progress" or "Testing" with `failed` status.
4. **Upcoming:** Check "Planned" lane for next cards to pick up.

Format:

```
## Standup — YYYY-MM-DD

### In Progress (<N> cards)
- [<card title>](vault://projects/<slug>/kanban.md) — <status note>
- ...

### Done since yesterday
- <card title> — <brief summary>
- ...

### Blocked / Issues
- <card title> — <what's blocking>
- ...

### Next up (Planned)
- <card title> — <priority>
- ...
```

## Card body conventions

Card bodies should contain:

1. **Description** — what this card represents (2-4 sentences).
2. **Spec link** — `vault://projects/<slug>/specs/<name>.md` (if spec exists).
3. **Acceptance criteria** — bulleted, observable, testable.
4. **Progress notes** — timestamped updates during implementation:
   ```
   - YYYY-MM-DD: Branch created feat/auth-login. RED tests written.
   - YYYY-MM-DD: Implementation complete, all tests GREEN. Awaiting review.
   ```
5. **Metadata comments** — always preserve `<!-- nx:... -->` lines:
   - `<!-- nx:id=<uuid> -->` (required, never edit)
   - `<!-- nx:session=<sid> -->` (auto-linked on dispatch)
   - `<!-- nx:branch=feat/<slug> -->` (linked on branch creation)
   - `<!-- nx:pr=https://github.com/.../pull/N -->` (linked on PR creation)
   - `<!-- nx:status=<running|done|failed> -->` (kept in sync with execution)
   - `<!-- nx:due=YYYY-MM-DD -->`
   - `<!-- nx:priority=<low|med|high|urgent> -->`
   - `<!-- nx:labels=<comma-separated> -->`

## Anti-patterns

- **Never delete cards.** Move to "Done" or create an "Archive" lane. Cards are the audit trail.
- **Never lose `<!-- nx:... -->` comments** when editing card body. These are the structured metadata the tools rely on.
- **Never create duplicate cards** for the same work. Search first with `kanban_query q="..."`.
- **Never move a card to "In Progress" without a clear owner** (assignee or self-assigned).
- **Never skip the spec** for complex features. Simple bug fixes can skip specs; anything with 3+ acceptance criteria should have one.
- **Never auto-move to "Done"** if the implementation card failed. Surface the failure and let the user decide.

## Cross-project queries

When the user asks about work across all projects:

```
kanban_query status=running              → all in-progress work
kanban_query priority="urgent"           → urgent items across all boards
kanban_query labels=["blocker"]          → blockers everywhere
kanban_query due_before="2025-02-01"     → overdue items
```

Group results by project/board name. Highlight overdue and blocked items first.
