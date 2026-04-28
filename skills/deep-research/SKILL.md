---
name: deep-research
description: Use this whenever you need to produce a thorough, source-backed research answer on any topic. Prefer over simple web_search for queries that require evidence evaluation, comparison, credibility assessment, or health/product/legal subject matter. Fans out to parallel sub-agents so search results and scraped pages stay in their own contexts — the parent only sees structured findings.
---

## When to use
- Any query where the answer needs to cite real sources with URLs
- Health, supplement, product, or medical topics (always)
- Comparisons, reviews, or "vs" queries
- Legal, financial, or safety-critical subjects
- When the user asks for a deep dive, analysis, or "what does the evidence say"
- NOT for: simple factual lookups, math, coding, creative writing

## Execution model

This skill uses `spawn_subagents` in **two phases** to keep the parent context clean while going deeper than a single fan-out can.

- **Phase 1 — breadth.** 3–5 sub-agents cover distinct angles in parallel. Each returns a compact structured summary. The parent never sees raw scraped content.
- **Phase 2 — depth.** Read the phase-1 findings, identify contradictions / weak-evidence claims / gaps, and fan out a second time with focused follow-up prompts. Skip phase 2 only if phase 1 surfaced no contradictions or weak-evidence claims worth chasing.

After both phases the parent has a small, structured pile of findings — typically 5–15 KB of text — and writes the final research **to the vault as the canonical artifact**. The chat reply is just a bottom-line + a link to the vault file.

**Why fan out twice:** A single breadth pass surfaces claims but not their cross-source verification. Phase 2 closes that loop without bloating the parent: each follow-up sub-agent does its own scraping in its own context.

**Why vault-as-canonical:** A deep-research output is a document, not a chat message. Putting it in the vault makes it linkable, searchable, re-usable, and editable. The chat thread becomes a pointer, not the artifact.

## Steps

### 1. Decompose into research angles

Pick 3–5 sub-questions that cover the topic from different perspectives. Adjust angles to the domain:

**Health / supplements:**
- `[topic] clinical evidence peer reviewed`
- `[topic] criticism limitations side effects`
- `[topic] FDA regulation`

**Products / brands:**
- `[topic] review independent`
- `[topic] vs [competitor] comparison`
- `[topic] scam criticism complaints`

**General research / news:**
- `[topic] overview analysis primary sources`
- `[topic] latest news developments`
- `[topic] expert opinion criticism`

Always include at least one angle aimed at criticism, limitations, or negative coverage.

### 2. Phase 1 — breadth fan-out via `spawn_subagents`

Call `spawn_subagents(tasks=[...])` with one task per angle (max 8). Each `prompt` must be **self-contained** — the sub-agent has zero memory of this conversation. Use this template literally for each task's prompt:

```
Topic: <subject of investigation>
Angle: <this sub-agent's specific angle, e.g. "criticism + limitations">

Do the following, then return a STRUCTURED SUMMARY:
1. Run 1–2 web_search calls scoped to this angle.
2. From the results, scrape 3–5 of the highest-credibility unique-domain sources
   using web_scrape with output_format=markdown. Prioritize: peer-reviewed,
   government, established independent publications, then company/PR/affiliate.
3. Read each scraped source. Extract concrete claims with attribution.
4. Return ONLY the structured summary below — DO NOT dump raw content,
   DO NOT paste full articles, DO NOT include intermediate search results.

Return format (markdown):

## Findings (angle: <angle>)

### Key claims
- Claim — source URL — credibility tag (peer-reviewed/government/independent/
  company/press-release/affiliate/anecdotal) — note any conflicts of interest
- ...

### Contradictions or caveats
- <contradictions between sources, study design issues, missing evidence>

### Sources read
- [URL] — [Title] — [credibility tag] — [scrape ok | snippet only]

Cap the entire response at ~500 words. Be terse. No filler.
```

Pick `name` short and descriptive (`evidence`, `criticism`, `regulation`, `comparison`, ...) — it appears in the parent's tool result so synthesis can cite which angle surfaced what.

### 3. Phase 2 — depth fan-out (targeted follow-ups)

Read the phase-1 findings and decide whether a second pass is warranted. Look for:
- **Contradictions** between angles (sub-agent A says X, sub-agent B says ~X).
- **Single-source claims** — important assertions backed by only one source, especially if that source is `company`, `press-release`, or `affiliate`.
- **Critical gaps** — questions central to the user's query that no phase-1 sub-agent answered.
- **Weak-evidence flags** — claims tagged as ingredient-level only, open-label, small-N, or industry-funded where independent verification matters.

If you find none of the above, skip phase 2 and go to step 4.

Otherwise, spawn another `spawn_subagents(tasks=[...])` call with 2–4 **focused** tasks. Each task targets one specific contradiction, claim, or gap — not a broad angle. Use this template:

```
Topic: <subject>
Specific question: <single, narrow question — e.g. "Is X claim from source Y
contradicted by independent peer-reviewed evidence?">

Context (what the breadth pass already found): <2-4 sentences summarizing the
relevant phase-1 findings — keep this terse, do NOT paste full sub-agent
output>

Do the following, then return a STRUCTURED SUMMARY:
1. Run 1–2 narrow web_search calls (or scrape specific URLs if already known).
2. Read the most authoritative 2–3 sources for this specific question.
3. Return:

## Verification (question: <question>)

### Verdict
- one of: confirmed | contradicted | mixed | inconclusive
- one-sentence rationale

### Evidence
- bullet list of concrete claims with source URLs and credibility tags

### Sources read
- [URL] — [Title] — [credibility tag]

Cap at ~400 words. Be specific about which claim you verified or refuted.
```

Phase 2 sub-agents are smaller and faster than phase-1 sub-agents — they have a narrow question, not a whole angle.

#### Adversarial sub-agent (default-on)

In addition to the verification tasks above, **add one more task to the same `spawn_subagents` call**: an adversarial sub-agent whose explicit job is to attack the phase-1 findings — find what got missed, framed too charitably, or quietly contradicted.

**Run the adversarial task by default. Skip it only when ALL of these are true:**
- The topic is a neutral factual lookup (history, definition, technical concept)
- No commercial actor stands to gain from any framing of the answer
- No specific product, service, person, or organization is being evaluated
- The user is not making a purchase, treatment, investment, legal, or safety decision based on the answer

**ALWAYS run it when ANY of these are true** (these override the skip conditions above):
- Health, supplement, medical, drug, or wellness topic
- Product, brand, or service evaluation (incl. "X review", "X vs Y", "is X legit", "should I buy/use X")
- Legal, financial, tax, or regulatory subject
- Safety-critical: nutrition, exercise, parenting, home/auto repair affecting safety
- Phase-1 results contain >50% `company`/`press-release`/`affiliate` sources
- Phase-1 findings contain visible contradictions between angles
- Topic involves a publicly-traded company (deals, earnings, restructurings, M&A)
- Politically or socially contested topic

When in doubt, run it. The cost of one extra phase-2 sub-agent is much smaller than the cost of shipping a one-sided answer.

Adversarial task prompt template:

```
Topic: <subject>

Phase-1 findings (terse — 2 sentences per angle, NO full content):
- <angle-1>: <2-sentence summary>
- <angle-2>: <2-sentence summary>
- ...

Your job: red-team this research. The other sub-agents covered their angles
charitably. Find what they missed, framed too gently, or quietly contradicted.

Look specifically for:
- Retracted, corrected, or disputed studies
- Settled lawsuits, consent decrees, regulatory settlements
- FDA warning letters, recalls, NDA violations, agency actions
- Financial restatements, going-concern warnings, SEC filings
- Conflict-of-interest disclosures buried in author affiliations or funding
- Counter-research from competitors, independent labs, or critics
- Journalistic exposés or investigative reports the angles missed
- Major topics or stakeholders the angles didn't cover at all

Method:
1. Run 2–3 web_search calls aimed at the negative space — phrases like
   "<topic> retracted", "<topic> lawsuit settled", "<topic> warning letter",
   "<topic> FTC", "<topic> criticism", "<topic> failed".
2. Scrape 2–4 sources that returned hits.
3. Return:

## Adversarial findings

### Issues identified
- <Concrete issue> — source URL — credibility tag — severity (high/medium/low)
- ...

### Topics the phase-1 angles did not cover
- <Topic> — why it matters — source URL if any
- ...

### Verdict
- One of: significant-issues-found | minor-issues | nothing-substantive-found
- One sentence summarizing the strongest counter-evidence

### Sources read
- [URL] — [Title] — [credibility tag]

Cap at ~500 words. If you find nothing meaningful after honest searching,
say "nothing-substantive-found" — do NOT manufacture concerns to fill space.
```

### 4. Aggregate the source map

After phase 1 (and phase 2 if it ran), build a single combined source map by walking the `Sources read` sections from every sub-agent:

```
[N] Title — URL — source_type — angle_name
```

Where `source_type` is one of: `peer-reviewed`, `government`, `independent`, `company`, `press-release`, `affiliate`, `anecdotal`. De-duplicate URLs that appear in multiple sub-agents.

### 5. Credibility assessment & conflict-of-interest check

Tally the source mix. If >50% of sources are `company`, `press-release`, or `affiliate`, add this warning at the top of the vault file (NOT just the chat reply):

> Note: Available sources for this topic are primarily from the company, its marketing channels, and affiliate review sites. Independent verification is limited.

### 6. Evidence triage — before writing

Run this checklist against the aggregated findings (NOT against raw scraped content — that lives in the sub-agents):

1. **Product-level vs. ingredient-level evidence**: Did the company cite studies on the *finished product*, or on individual ingredients? Flag if only ingredient-level.
2. **Study design quality**: Were the cited studies placebo-controlled? Double-blind? Open-label? How many participants? Flag weak designs.
3. **The Lady Prelox problem**: Does the company cite a study on a *different product* with overlapping ingredients as evidence for its own product? Common in supplements — flag it.
4. **Funding source**: Were the studies funded by the company that sells the product? Say so.
5. **Contradictions between angles**: When sub-agents disagree, surface the contradiction directly. This is one of the biggest wins of fan-out research.
6. **Missing evidence**: Are there obvious questions the sub-agents couldn't answer? State what's missing rather than papering over it.
7. **Adversarial findings**: If the adversarial sub-agent ran, integrate its findings — don't quarantine them. Each `Issues identified` item must either be (a) reflected in the synthesis as a caveat / counter-evidence to the relevant claim, or (b) explicitly addressed and rebutted with a citation. Do NOT silently drop adversarial findings just because they conflict with the dominant narrative. The `Topics the phase-1 angles did not cover` section often points at the most important holes — fill them in the vault file or note them as open questions.

If any sub-agent returned an `error` (non-null in the tool result), mention it briefly to the user and proceed with the partial findings — don't silently retry.

### 7. Write the canonical research file → vault

The final research goes to the vault. The chat reply is a pointer, not the artifact.

**Vault path**: `research/<slug>-<YYYY-MM-DD>.md` where `<slug>` is a kebab-case 2–5-word topic. Use `vault_write` to create it.

**Required frontmatter:**
```yaml
---
tags: [<topic-tags>, <domain-tags>]
created: <YYYY-MM-DD>
session: <current-session-id>
---
```

**Required body structure (in this order):**
1. `# <Title>` — descriptive title, not the user's raw query
2. **Bottom line** (1–2 paragraphs) — the synthesized answer up front. NO header for this section, just lead with prose.
3. The COI warning from step 5 if it applies.
4. `##` sections covering each major facet of the answer. Use:
   - Markdown tables for comparisons
   - Flat unordered lists (no nesting)
   - Bold text (`**`) for subsections within sections (not `###`)
   - Never a list with a single item
5. **For health/product queries**, include:
   - A regulatory status table (FDA classification, approval status, DSHEA, etc.)
   - Both benefits AND limitations for any product discussed
   - Clear distinction between "clinically studied" and "clinically proven"
6. **Caveats & counter-evidence** section (`## Caveats`) — REQUIRED when the adversarial sub-agent returned `significant-issues-found` or `minor-issues`. Lists the concrete issues, scoped by severity, with citations. May be omitted only when adversarial returned `nothing-substantive-found` OR when the adversarial task was legitimately skipped per step 3's skip rules.
7. Inline `[N]` citations throughout (see step 8).
8. `## Sources` section at the end (see step 11).

**Length**: long enough to be the canonical reference. Don't compress just because the chat reply will be short. A typical deep-research file is 800–2500 words.

### 8. Citation discipline

- Cite inline using `[N]` immediately after the claim sentence
- Max 3 citations per sentence
- No space between last word and citation
- Every `[N]` must correspond to a real source from the aggregated source map
- NEVER fabricate citation indices
- NEVER cite a URL no sub-agent reported reading

When a claim came from a specific angle, you may attribute it inline (e.g. "the criticism angle surfaced..."). This is optional but useful when angles disagree. When a phase-2 verification confirmed or contradicted a phase-1 claim, surface that explicitly in the prose.

### 9. Uncertainty signaling (NOT hedging)

Banned (filler hedging): "It is important to note...", "It is worth mentioning...", "Interestingly...", "It should be noted that..."

Required (epistemic honesty — use when evidence is genuinely weak):
- "Evidence for this claim is limited to [industry-sponsored / small / uncontrolled] studies."
- "This claim is primarily supported by the company's own research."
- "Clinical data comes from studies on [ingredient X], not on the finished product itself."
- "No large-scale, independent, long-term trials exist for this specific product."
- For supplements: always state "This is a dietary supplement, not an FDA-approved medication."
- For health queries: always include "Consult a healthcare provider before starting any supplement regimen."

### 10. Self-check before writing the vault file

Verify:
1. Every claim has at least one citation OR is stated as general knowledge
2. No citation index exceeds the number of sources in your aggregated map
3. The vault file addresses ALL parts of the query — re-read the original query
4. For products: both benefits AND limitations are present
5. No paragraph makes a definitive health claim without citing evidence
6. The vault file makes sense to a reader who cannot see the sub-agent transcripts
7. Benefits claims from company sources are counterbalanced with independent analysis
8. Tables render correctly (headers defined, columns aligned)
9. If any sub-agent errored, this is mentioned (briefly) so the reader can judge completeness
10. Phase-2 verifications (if run) are reflected in the prose, not silently dropped
11. **Adversarial findings (if run) are reflected in the vault file** — either integrated as caveats with citations, or rebutted with independent counter-evidence. Never silently dropped. If adversarial returned `nothing-substantive-found`, that fact may be stated briefly ("No retracted studies, regulatory actions, or significant counter-evidence surfaced.") rather than omitted entirely
12. **The adversarial decision is auditable** — if the adversarial task was skipped, the topic clearly meets ALL skip criteria from step 3. If unsure, it should have run.

### 11. Sources section

End with a `## Sources` section. Format each entry:
```
[N] Title — URL (credibility label, optional note)
```
Examples:
```
[1] Healthline — "Bonafide Review" — https://... (independent, medically reviewed)
[5] PR Newswire — "Bonafide introduces Ristela data" — https://... (company press release)
[9] U.S. FDA — "Dietary Supplements" — https://... (government)
```

Only list sources you actually cited inline. No padding with uncited sources.

### 12. Chat reply — pointer, not deliverable

After the vault file is written, your reply to the user is short:

1. Open with a markdown link to the vault file using `vault://` href:
   `Research saved to [<title>](vault://research/<slug>-<date>.md).`
2. Follow with a 2–4 sentence **bottom line** — the synthesized answer's headline finding. NOT a summary of the whole document; the headline.
3. Optionally, one sentence flagging the strongest caveat or the most surprising contradiction surfaced (especially if phase 2 changed the picture or the adversarial sub-agent surfaced significant issues). When adversarial returned `significant-issues-found`, this sentence is **required**, not optional.
4. If any sub-agent errored, mention it in one sentence so the user knows the file is N-1 angles deep.

DO NOT paste the full research into chat. DO NOT repeat the bullet structure. The vault file IS the artifact; the chat reply is a pointer to it.

## Gotchas

- **The Lady Prelox problem**: Companies sometimes cite studies on a *different product* with overlapping ingredients as evidence for their own product. Check whether the cited study was actually conducted on the named product. Sub-agents are instructed to flag this; double-check during triage.
- **Open-label vs. placebo-controlled**: Many supplement trials are open-label (no placebo group, no blinding). These dramatically overestimate effect sizes. Flag whenever detected.
- **Affiliate review sites dominate search results**: For product queries, the first 10 Google results are often affiliate sites. Multiple angles + sub-agent diversity mitigates this, but stay vigilant during triage.
- **Company-funded research**: If every study on a product was paid for by the company that sells it, say so explicitly. Standard in the supplement industry but the reader needs to know.
- **"Clinically studied" ≠ "clinically proven"**: Companies use "clinically studied" to imply proof. The studies may be small, uncontrolled, or irrelevant to the final formulation. Always distinguish ingredient-level vs. product-level evidence.
- **Sub-agent dump**: If a sub-agent returns a wall of raw content instead of the structured format, treat it as a partial result — extract what you can but note the format failure briefly to the user.
- **Sub-agent error**: If a sub-agent's `error` field is non-null, you have N-1 angles. Decide: ship a partial answer with a note, or call `spawn_subagents` again for just the missing angle. Don't silently fail.
- **Sequential follow-up search**: The parent context has `web_search` available too. After the fan-out, if synthesis reveals one specific factual gap, a single targeted `web_search` from the parent is fine — but resist the urge to scrape: dumping raw markdown into the parent is exactly what the fan-out was meant to avoid. Better: phase-2 sub-agent.
- **Skipping phase 2 when warranted**: Phase 2 is not a ritual. If phase 1 returned consistent, well-sourced findings with no contradictions and no critical gaps, go straight to writing the vault file. Don't burn tokens spawning verification sub-agents to confirm what's already solid.
- **Phase 2 scope creep**: Phase-2 prompts must be narrow ("verify claim X", "resolve contradiction between A and B"). If you're tempted to write a broad phase-2 prompt, that's a sign you should have framed it as a phase-1 angle from the start.
- **Vault-file vs. chat split**: If you find yourself writing substantive analysis in the chat reply, stop — that content belongs in the vault file. The chat reply is link + bottom line + caveat. Five sentences max.
- **Adversarial laundering**: An adversarial sub-agent returning `significant-issues-found` is a *signal*, not noise. Resist the urge to bury its findings in a footnote or soften them with "however, on balance..." prose. If a settled lawsuit, retracted study, or warning letter exists, it goes in `## Caveats` with the same `[N]` citation discipline as the rest. The reader needs the same access to the counter-evidence the agent had.
- **Adversarial false positives**: The adversarial sub-agent is instructed to say `nothing-substantive-found` rather than manufacture concerns, but it can still over-flag. If an "issue" is a minor 8-year-old class action that settled with no admission of wrongdoing, it's `low` severity, not equal billing. Severity tagging exists for this — use it.
- **Skipping adversarial**: The skip rules in step 3 are deliberately strict (ALL conditions must hold). If you find yourself reasoning "this looks neutral *enough*", that's not strict enough. Run it. The cost of one extra sub-agent is ~$0.01-0.10 in tokens; the cost of shipping a one-sided answer on a contested topic is your credibility.
