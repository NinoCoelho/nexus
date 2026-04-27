---
name: deep-research
description: Use this whenever you need to produce a thorough, source-backed research answer on any topic. Prefer over simple web_search for queries that require evidence evaluation, comparison, credibility assessment, or health/product/legal subject matter.
---

## When to use
- Any query where the answer needs to cite real sources with URLs
- Health, supplement, product, or medical topics (always)
- Comparisons, reviews, or "vs" queries
- Legal, financial, or safety-critical subjects
- When the user asks for a deep dive, analysis, or "what does the evidence say"
- NOT for: simple factual lookups, math, coding, creative writing

## Steps

### 1. Broad parallel search
Run 3–5 parallel `web_search` calls with different query angles. Adjust the angles to the domain:

**Health / supplements:**
- `[topic] clinical evidence peer reviewed`
- `[topic] criticism limitations side effects`
- `[topic] FDA regulation`

**Products / brands:**
- `[topic] review independent`
- `[topic] vs [competitor] comparison`
- `[topic] scam criticism complaints`

**General research:**
- `[topic] overview analysis`
- `[topic] latest news developments`
- `[topic] expert opinion`

Always include at least one query angled toward criticism, limitations, or negative coverage.

Collect ALL results into a single numbered source map:
```
[N] = Title — URL — source_type
```
Where source_type is one of: `peer-reviewed`, `government`, `independent`, `company`, `press-release`, `affiliate`, `anecdotal`.

### 2. Automated bulk scrape
Scrape as many sources as practical — aim for ALL unique domains from the search results, minimum 4, no maximum. Prioritize by credibility:

1. Independent health/science publications (Healthline, MNT, Cochrane, peer-reviewed journals)
2. Government agency pages (FDA, NIH, WHO, CDC)
3. Established news outlets with editorial oversight
4. Independent reviewers who cite primary sources
5. Company pages and press releases (lower priority but scrape for claim verification)

Use `web_scrape` with `output_format: markdown` for all. Run in parallel batches when possible.

If a scrape fails or returns only JS/boilerplate, fall back to the search snippet for that source and tag it `[snippet only]` in the source map.

### 3. Credibility assessment
Tag every source with a credibility label:

| Label | Meaning |
|---|---|
| `peer-reviewed` | Published in a journal with editorial oversight |
| `government` | FDA, NIH, CDC, WHO, etc. |
| `independent` | Established publication with editorial standards (Healthline, MNT, Wirecutter) |
| `company` | The subject company's own website or blog |
| `press-release` | PR Newswire, Business Wire, etc. |
| `affiliate` | Review sites that earn commissions from clicks |
| `anecdotal` | User reviews, testimonials, Reddit |

**Conflict-of-interest check:** If >50% of sources are `company`, `press-release`, or `affiliate`, add this warning at the top of the answer:

> Note: Available sources for this topic are primarily from the company, its marketing channels, and affiliate review sites. Independent verification is limited.

### 4. Evidence triage — before writing, evaluate
Before drafting the answer, run this mental checklist against the scraped content:

1. **Product-level vs. ingredient-level evidence**: Did the company cite studies on the *finished product*, or on individual ingredients? Flag if only ingredient-level.
2. **Study design quality**: Were the cited studies placebo-controlled? Double-blind? Open-label? How many participants? Flag weak designs.
3. **The Lady Prelox problem**: Does the company cite a study on a *different product* with overlapping ingredients as evidence for its own product? This is common in supplements — flag it.
4. **Funding source**: Were the studies funded by the company that sells the product? Say so.
5. **Contradictions**: Do independent sources contradict company claims? Surface those contradictions directly.
6. **Missing evidence**: Are there obvious questions the sources don't answer? State what's missing.

### 5. Write the answer — structure
Open with 2–3 sentences summarizing the answer. NEVER start with a header.

Use this structure:
- `##` for major sections
- Bold text (`**`) for subsections within sections (not `###`)
- Markdown tables for comparisons — preferred over long lists
- Flat unordered lists (no nesting)
- Never a list with a single item

**Health/product queries must include:**
- A regulatory status table (FDA classification, approval status, DSHEA, etc.)
- Both benefits AND limitations for any product discussed
- Clear distinction between "clinically studied" and "clinically proven"

### 6. Citation discipline
- Cite inline using `[N]` immediately after the claim sentence
- Max 3 citations per sentence
- No space between last word and citation
- Every `[N]` must correspond to a real, listed source you actually scraped or read
- NEVER fabricate citation indices
- NEVER cite a source you haven't actually read or scraped

### 7. Uncertainty signaling (NOT hedging)
Banned (filler hedging): "It is important to note...", "It is worth mentioning...", "Interestingly...", "It should be noted that..."

Required (epistemic honesty — use when evidence is genuinely weak):
- "Evidence for this claim is limited to [industry-sponsored / small / uncontrolled] studies."
- "This claim is primarily supported by the company's own research."
- "Clinical data comes from studies on [ingredient X], not on the finished product itself."
- "No large-scale, independent, long-term trials exist for this specific product."
- For supplements: always state "This is a dietary supplement, not an FDA-approved medication."
- For health queries: always include "Consult a healthcare provider before starting any supplement regimen."

### 8. Self-check before delivering
Verify:
1. Every claim has at least one citation OR is stated as general knowledge
2. No citation index exceeds the number of sources in your map
3. The answer addresses ALL parts of the query — re-read the original query
4. For products: both benefits AND limitations are present
5. No paragraph makes a definitive health claim without citing evidence
6. The answer makes sense to a reader who cannot see your search results
7. Benefits claims from company sources are counterbalanced with independent analysis
8. Tables render correctly (headers defined, columns aligned)

### 9. Sources section
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

## Gotchas
- **The Lady Prelox problem**: Companies sometimes cite studies on a *different product* with overlapping ingredients as evidence for their own product. Always check whether the cited study was actually conducted on the named product.
- **Open-label vs. placebo-controlled**: Many supplement trials are open-label (no placebo group, no blinding). These dramatically overestimate effect sizes. Flag this whenever detected.
- **Affiliate review sites dominate search results**: For product queries, the first 10 Google results are often affiliate sites. The bulk scrape step mitigates this by pulling from multiple query angles, but stay vigilant.
- **Company-funded research**: If every study on a product was paid for by the company that sells it, say so explicitly. This is standard in the supplement industry but the reader needs to know.
- **"Clinically studied" ≠ "clinically proven"**: Companies use "clinically studied" to imply proof. The studies may be small, uncontrolled, or irrelevant to the final formulation. Always distinguish between ingredient-level evidence and product-level evidence.
- **Scrape failures**: Some sites block scrapers or return only JavaScript. Fall back to search snippets, tag as `[snippet only]`, and note the limitation. Don't pretend you read the full page.
- **Context budget**: Bulk scraping uses context fast. If you're running low, prioritize scraping the highest-credibility sources and skip the low-value ones (affiliate pages, duplicate domains). Better to have 4 solid sources than 12 shallow ones.
