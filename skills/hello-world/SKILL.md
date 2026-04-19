---
name: hello-world
description: Briefly greet the user and ask what they want to work on. Use when the user opens a new chat with no specific ask.
type: procedure
role: onboarding
platform: nexus
platform_version: "0.1"
nexus_status: stable
nexus_authored_by: builtin
---

# hello-world

When the user opens a new chat with no specific task, say exactly:

> Hi — what can I help you with?

Do **not** list your tools, skills, or capabilities. Do **not** mention being "Nexus". Do **not** offer a menu. Just the greeting. Wait for the user's actual request.

## When to create a new skill

If the user asks you to do something and no existing skill covers it, attempt the task directly. If the task takes more than ~5 tool calls or you recovered from a non-trivial error, persist the procedure via `skill_manage` with action `create`.
