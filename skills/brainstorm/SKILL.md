---
name: brainstorm
description: Generate a structured set of ideas around a problem statement, then narrow them. Use when the user asks "give me ideas for…", "what could we try for…", or wants to explore a design space.
type: procedure
role: thinking
platform: nexus
platform_version: "0.1"
nexus_status: stable
nexus_authored_by: builtin
---

# brainstorm

Use when the user wants creative options, not a single answer. Skip when the question has a known correct answer (use `web-research` or direct knowledge instead).

## Procedure

1. **Frame.** Restate the problem in one sentence and list 2–3 constraints you're inferring. Ask the user to correct if any constraint is wrong before continuing — but only if there's real ambiguity.
2. **Diverge.** Produce 7–10 ideas. Mix safe and bold. Each idea: one-line label + one-line "how it would work". Number them.
3. **Cluster.** Group the ideas into 2–4 themes (one short label per theme).
4. **Converge.** Pick the top 3 ideas using these criteria, in order: (a) impact if it works, (b) effort to validate cheaply, (c) reversibility. Explain each pick in one sentence.
5. **Next step.** Suggest a single concrete next action the user could take in the next 24 hours to validate the top pick.

## Constraints

- Don't editorialise during the diverge step — saving judgement for converge keeps the option space wide.
- If the problem is under-specified, ask one clarifying question at frame time, not later.
- Stay platform-agnostic unless the user has signalled a specific stack.
