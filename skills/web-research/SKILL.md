---
name: web-research
description: Research a topic on the web and return a concise summary with cited sources. Use when the user asks for current facts, news, comparisons, or anything you cannot answer from training alone.
type: procedure
role: research
platform: nexus
platform_version: "0.1"
nexus_status: stable
nexus_authored_by: builtin
---

# web-research

Use when the user asks a factual or current-events question that benefits from live web data. Skip when the answer is obviously already in this conversation, the vault, or general knowledge.

## Procedure

1. Form 1–3 focused search queries from the user's question. Prefer specific phrasing over broad terms.
2. Call `web_search` for each query. If the tool is unavailable (no provider configured), tell the user once and offer to fall back on training-only knowledge.
3. Review search snippets first. Most research questions can be answered from snippets alone — they already contain summaries and key facts.
4. If a snippet is thin but the result looks critical, call `web_scrape` on **at most 1** URL. Only scrape a second URL if the first scrape returned no usable content. Pass `max_content_chars: 5000` to keep context lean.
5. Synthesise a tight answer. Lead with the conclusion, then 2–4 supporting bullets. Mark anything uncertain or contested.
6. End with a `## Sources` section listing each citation as `- [Title](URL) — one-line why this source`.

## Constraints

- Do not invent citations. If you didn't fetch a URL, do not list it.
- If sources disagree, surface the disagreement instead of picking arbitrarily.
- Keep the final answer under ~250 words unless the user asked for depth.
- Never scrape more than 2 URLs per research task. Prefer search snippets over full scrapes.
- Always pass `max_content_chars: 5000` when scraping for research to avoid bloating context.
- After scraping, immediately synthesize your answer. Do not chain additional scrapes to "verify" or "get more context" — work with what you have.
- If you need to verify a specific claim, prefer a targeted `web_search` query over another `web_scrape`.
