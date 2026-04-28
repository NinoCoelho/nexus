---
name: parallel-research
description: Use this when a research question benefits from N independent investigations whose intermediate work (searches, scrapes, tool calls) should NOT pollute the main conversation context. Spawns sub-agents in parallel; you only see the final synthesized answer from each.
---

## When to use

- A multi-angle question where each angle is independent (e.g. "compare A, B, C across dimensions X, Y, Z").
- A topic survey where you want 3–6 distinct framings explored at once.
- Any deep-research task where the parent's context is already large or where you want to keep the chat history clean — sub-agents do the heavy lifting (search → scrape → analyse) and return only conclusions.

NOT for:
- Single-shot factual lookups → call `web_search` directly.
- Tasks where each step depends on the previous answer → run them sequentially in your own loop instead.
- Anything that needs human approval mid-task — sub-agents have NO `ask_user` and cannot prompt the user.

## How it works

`spawn_subagents` runs N agent loops in parallel, each with:
- A fresh, empty context (the sub-agent has no memory of this chat).
- The full tool registry **except** `ask_user`, `terminal`, `dispatch_card`, and recursive `spawn_subagents`.
- Its own hidden child session (visible to you via `?include_hidden=true` on `/sessions` for debugging).

Only each sub-agent's final assistant text comes back to you — all their tool calls, search results, and intermediate reasoning stay in their own context.

## Workflow

### 1. Decompose
Break the user's question into 2–6 self-contained sub-questions. Each `prompt` must include all the scope a stranger would need: the sub-agent has zero context from this chat. Include constraints, definitions, and what shape the answer should take.

### 2. Spawn
```
spawn_subagents(tasks=[
  {"name": "angle-1", "prompt": "<self-contained instruction>"},
  {"name": "angle-2", "prompt": "<self-contained instruction>"},
  ...
])
```
Maximum 8 tasks per call. Use the optional `model_id` per task if a specific sub-task warrants a different model.

### 3. Synthesize
The tool returns `{"results": [{"name": ..., "result": "...", "session_id": "...", "error": null}, ...]}`. Read each `result`, cross-reference where they agree or disagree, and write a single integrated answer for the user. Cite which sub-agent surfaced what (by `name`).

If any `error` is non-null for a sub-task, mention it briefly to the user and proceed with the partial result set rather than retrying silently.

## Example decomposition

User: "Should I switch from X to Y?"

Good sub-tasks:
1. `name: "x-strengths"` — "Survey what X does well based on independent reviews and user reports from the last 12 months. Return a bulleted list with citations."
2. `name: "y-strengths"` — "Survey what Y does well based on independent reviews and user reports from the last 12 months. Return a bulleted list with citations."
3. `name: "migration-cost"` — "Investigate the practical cost of migrating from X to Y: data export/import, retraining, downtime. Return a paragraph + risk list."
4. `name: "y-criticism"` — "Find substantive criticism of Y from independent sources. Return a bulleted list with citations and severity assessment."

Bad sub-tasks:
- "Search for X" — too vague, no shape for the return.
- "Tell me about X" with the user's full message pasted in — the sub-agent doesn't need (or want) the chat history; give it only what it needs to answer.
