---
name: ontology-curator
description: Inspect or evolve the GraphRAG ontology (entity types + relations) when the user asks to "review", "extend", "clean up", or "propose changes to" the ontology, or analyze vault docs to suggest new types.
type: procedure
role: graphrag
platform: nexus
platform_version: "0.1"
nexus_status: stable
nexus_authored_by: builtin
---

# ontology-curator

Use whenever the user asks about the GraphRAG ontology — listing it, adding/removing types, or analyzing vault content to suggest improvements. The ontology lives at `~/.nexus/vault/_system/ontology/` (CSV + INSTRUCTIONS.md). Both the user and you can edit it via `ontology_manage`.

## When to use

- "Mostre / liste / show the ontology" → action `view`.
- "Adicione / add a type / relation called X" → action `add_type` or `add_relation`.
- "Atualize / update / rename X" → action `update_type` or `update_relation`.
- "Remova / drop / delete X" → action `remove_type` or `remove_relation`.
- "Analise meu vault e proponha melhorias / Look at my notes and suggest new types" → procedure below (ends in `propose_from_documents`).
- "A ontologia faz sentido?" → run `view`, summarize each type/relation, point out duplicates or low-yield categories.

Never edit the ontology files via `vault_write` directly — go through `ontology_manage` so the engine re-initializes and the change actually takes effect.

## Procedure — propose from documents

1. Call `ontology_manage` with `action="view"`. Read the returned `instructions` block carefully and note the existing types/relations.
2. Pick a focused slice of the vault: either the paths the user mentioned, or 5-15 candidate notes via `vault_search` / `vault_semantic_search`. Read them with `vault_read`. Avoid scanning the whole vault — too many docs blur the signal.
3. Identify gaps:
   - Concepts that recur across notes but get classified as the catch-all `concept`. These deserve a dedicated type.
   - Verb patterns ("X aprovou Y", "X causou Y", "X depende de Y") that the current relations can't capture.
   - DON'T propose types/relations that only appear in one note.
4. Build a structured proposal — prototypes in BOTH English and Portuguese (the embedder is multilingual, anchoring helps recall):
   ```json
   {
     "rationale": "Why these additions help — one short paragraph.",
     "entity_types": [
       {"type": "meeting", "description": "scheduled discussions / sync points", "prototypes_en": "meeting gathering session sync standup", "prototypes_pt": "reunião encontro sessão sincronização"}
     ],
     "relations": [
       {"relation": "approved_by", "description": "decision approved by an authority", "prototypes_en": "approved by sanctioned by signed off by", "prototypes_pt": "aprovado por sancionado por assinado por"}
     ]
   }
   ```
5. Call `ontology_manage` with `action="propose_from_documents"` and the `proposal` payload above. The tool renders a diff and asks the user to confirm via `ask_user`. On approval the additions are applied and GraphRAG re-initializes automatically.
6. If the user declines, do not retry silently — ask what they'd prefer instead.

## Gotchas

- snake_case names only (`approved_by`, not `approvedBy` or `approved-by`). The tool rejects anything else.
- Keep the ontology lean. < 20 types and < 25 relations is the comfortable zone; beyond that, similarity classification gets fuzzy.
- Existing extractions in the graph keep their old type labels. For a full re-extraction with the new ontology, tell the user to run `uv run nexus graphrag reindex` (it's not automatic — re-indexing the whole vault is expensive).
- After you change the embedder model (or the user does), `/graph/knowledge/health` may report a `stale_warning`. That's separate from ontology changes — it means the existing vectors were built with a different embedder. Ask the user if they want to run reindex.
- If `propose_from_documents` errors with "ask_user is unavailable", you're being called outside a chat session. Fall back to making CRUD calls (add_type / add_relation) one by one with the user's prior approval embedded in the conversation.
