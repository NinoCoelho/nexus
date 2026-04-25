---
name: summarize-file
description: Read a vault file and return a 5-bullet summary plus key entities and open questions. Use when the user wants the gist of a long markdown note, meeting log, or doc.
type: procedure
role: vault
platform: nexus
platform_version: "0.1"
nexus_status: stable
nexus_authored_by: builtin
---

# summarize-file

Use when the user asks for a summary, gist, TL;DR, or "what's in" a vault file. Also use proactively when the user references a long file by `vault://` link and the next step needs only a digest.

## Procedure

1. Resolve the path. If ambiguous, call `vault_list` or `vault_semantic_search` to disambiguate before reading.
2. `vault_read` the file. If it's larger than ~30 KB, read in segments and merge.
3. Output exactly:
   - **TL;DR** — one sentence that captures the file's purpose.
   - **Key points** — 3–5 bullets, each ≤20 words, ordered by importance.
   - **Entities mentioned** — up to 6 people / projects / systems, comma-separated.
   - **Open questions** — anything the file flags as unresolved (skip the section if there's nothing).
4. End with a `vault://` link back to the source file.

## Constraints

- Do not paraphrase quotes — preserve them with quotation marks.
- Do not invent entities or open questions; only surface what's in the text.
- Markdown tables, code blocks, and frontmatter count as content; summarise their gist, don't ignore them.
