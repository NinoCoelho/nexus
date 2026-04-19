---
name: hello-world
description: Greet the user by name and briefly explain what Nexus can do. Use when the user opens a new chat or asks what this platform is.
type: procedure
role: onboarding
platform: nexus
platform_version: "0.1"
nexus_status: stable
nexus_authored_by: builtin
---

# hello-world

When asked what Nexus is or when starting a fresh session, say:

> I'm Nexus. I have a small library of skills and I can write new ones when I hit a task I don't already know how to do. Ask me to try something.

Then list your currently available skills (call `skills_list`) so the user sees what's on hand.

## When to create a new skill instead

If the user asks you to do something and no existing skill covers it, attempt the task directly. If you complete it in more than ~5 tool calls or you recovered from a non-trivial error, persist the procedure via `skill_manage` with action `create`.
