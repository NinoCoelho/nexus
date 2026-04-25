---
name: vault-curator
description: Suggest tags, missing links, and reorganisations for a vault file. Use when the user asks to "clean up", "organise", "improve", or "add tags to" a note.
type: procedure
role: vault
platform: nexus
platform_version: "0.1"
nexus_status: stable
nexus_authored_by: builtin
---

# vault-curator

Use when the user wants help making a single vault file easier to find and link from elsewhere. Skip for whole-vault sweeps unless the user explicitly asks for one.

## Procedure

1. `vault_read` the target file and inspect its frontmatter.
2. Run `vault_semantic_search` on the file's main topic to find related notes already in the vault. Note 3–6 plausible candidates for cross-linking.
3. Propose, in this order:
   - **Tags to add or remove** — keep the list short (≤5 total tags). Justify each with one phrase.
   - **Wiki-links to add** — ≤4 suggestions, each pointing at an existing vault file by path. Include the anchor text you'd use in prose.
   - **Section moves / renames** — only when a heading is clearly mis-scoped or duplicated.
4. Ask "apply these?" before editing. On confirm, write changes via `vault_update`. On decline, leave the file untouched.

## Constraints

- Never suggest a wiki-link to a file you haven't seen exist via search or `vault_list`.
- Preserve the file's authorial voice — no rewording sentences unless the user asks.
- If the file is already well-tagged and well-linked, say so and stop. Don't invent work.
