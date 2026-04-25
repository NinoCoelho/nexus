---
name: code-review-local
description: Review the user's local code changes (uncommitted diff or current branch vs main) and surface concrete issues. Use when the user asks "review my changes", "what do you think of this code", or "before I push".
type: procedure
role: engineering
platform: nexus
platform_version: "0.1"
nexus_status: stable
nexus_authored_by: builtin
---

# code-review-local

Use when the user wants a focused pre-push review of code they're working on. Skip for design discussions, architecture questions, or post-merge retrospectives.

## Procedure

1. Determine the scope. Default to "uncommitted changes". If the user says "this branch" or names a base, diff against that base instead. Use `terminal` to run `git diff` or `git diff <base>...HEAD` (request approval; the user controls execution).
2. Read the diff with rough understanding of context — open the touched files via `terminal` `cat` or vault read if relevant.
3. Cluster findings into:
   - **Bugs / correctness** (highest priority — concrete, reproducible)
   - **Risk / regression**
   - **Maintainability** (naming, complexity, dead code)
   - **Style nits** (only if the user asked for them)
4. For each finding: file + line range + one-paragraph diagnosis + suggested fix (concrete code or prose). Reference paths as `path/to/file.ts:42`.
5. End with a one-line verdict: "ship it", "fix these N issues first", or "needs more thought".

## Constraints

- Do not invent line numbers — only cite lines that appear in the diff.
- Do not run linters or tests unless the user agrees.
- Keep nits separate from bugs. A reviewer who buries a real bug under nits is unhelpful.
- If the diff is empty, say so and stop. Don't pretend there's something to review.
