---
name: nexus
description: Use when the user asks how to do something in Nexus itself — configure a model, change theme/colors, update the vault GraphRAG index, manage skills, set up the public tunnel, back up data, understand the demo model, etc. Trigger phrases include "how do I", "where do I configure", "in Nexus how do you", "what's the setting for", and any meta-question about the app.
type: procedure
role: research
platform: nexus
platform_version: "0.1"
nexus_status: stable
nexus_authored_by: builtin
---

# nexus

Use this skill when the user is asking about Nexus itself — configuration, settings, features, operational tasks. Skip when the question is about the user's own project content (use the vault tools instead) or about general programming.

## Procedure

1. Call `nexus_kb_search` with a short keyword query distilled from the user's question (e.g. "configure model openai", "change theme color", "update graphrag index"). Default `k=3` is fine; raise to 5 only if the first result looks weak.
2. Read the returned chunks. Each chunk is one section of the bundled `knowledge.md` and is self-contained.
3. Answer the user from the retrieved chunks. Quote concrete file paths, config keys, and CLI commands verbatim — don't paraphrase them.
4. If the chunks don't cover the question, say so plainly and suggest the user check `~/.nexus/config.toml` directly or the Settings drawer in the UI. Do **not** invent config keys.

## Constraints

- Treat `nexus_kb_search` results as authoritative for "how does Nexus work today" questions. Your training data may be older than the running build.
- Cite the section header (the `## ` title from `knowledge.md`) when answering, so the user can find it themselves later.
- For destructive operations (deleting vault files, dropping the index, force-pushing), confirm with `ask_user` before running anything via the `terminal` tool.
- The knowledge file lives at `~/.nexus/skills/nexus/knowledge.md`. The user can edit it; if they do, a process restart picks up the changes (the BM25 index is built once per process).
