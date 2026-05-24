---
name: iterative-carousel-coordinator
description: THE entry point for all LinkedIn carousel creation. Coordinates carousel-copywriter and carousel-reviewer through 3 iterations with quality scoring, returning the best version. Never call carousel-copywriter directly.
type: procedure
role: editorial
platform: nexus
platform_version: "0.1"
nexus_status: stable
nexus_authored_by: builtin
---

## When to use

- **ALWAYS when a user asks for a LinkedIn carousel** -- this is the only skill that should handle carousel requests
- Creating carousels from scratch (from topics, news, source material)
- Improving an existing carousel draft through iterative review
- Any time you want "draft, review, revise, pick the best" for carousel content
- **DO NOT call carousel-copywriter directly** -- it is an internal skill called only by this coordinator

## Entry Point Rule

**Carousel-copywriter and carousel-reviewer are INTERNAL ONLY.**

- Users ask for carousels -> You call **iterative-carousel-coordinator**
- Coordinator handles the full workflow:
  - Creates initial draft using carousel-copywriter
  - Runs carousel-reviewer assessments
  - Generates revisions through carousel-copywriter
  - Compares versions, selects winner, returns results
- You never call carousel-copywriter or carousel-reviewer directly

## Steps

### Phase 0: Creation

If the user provides a topic/news/source but no draft:

1. **Run carousel-copywriter** to create the initial draft
   - Provide the source material (news summary, topic notes, facts)
   - Use the full carousel-copywriter framework (10-slide structure, Sharp Cut style)
   - Generate a complete carousel as Version 1

2. **Proceed to Phase 1 with Version 1**

If the user provides an existing draft, **skip Phase 0 and start at Phase 1.**

### Phase 1: Initial Review

- Run **carousel-reviewer** on Version 1
- Capture:
  - Overall score (/10)
  - Swipe power rating
  - Hook pass/fail
  - Cut strength pass/fail
  - Sharp Cut adherence (/25)
  - Critical issues list
  - Strengths to preserve

Store as **Version 1** with metadata.

### Phase 2: First Revision

Create revision prompt for **carousel-copywriter**:

```
Here is a carousel that needs revision. Please rewrite it addressing these specific issues:

[Reviewer feedback from Version 1 -- critical issues and priority fixes]

Preserve these strengths:
[Strengths from Version 1 review]

Original carousel:
[Version 1 full text with all slides]

Please return only the revised carousel following the standard output format, no commentary.
```

- Run **carousel-copywriter** to generate Version 2
- Run **carousel-reviewer** on Version 2
- Capture same metrics as Version 1

### Phase 3: First Comparison

| Version | Score | Swipe Power | Cut Pass | Adherence |
|---------|-------|-------------|----------|-----------|
| V1 | [score] | [score] | [pass/fail] | [score]/25 |
| V2 | [score] | [score] | [pass/fail] | [score]/25 |

**If V2 score > V1 score:** Proceed with V2 as base for next revision.

**If V2 score <= V1 score:** Flag regression. Note V1 is currently best. Proceed anyway.

### Phase 4: Second Revision

Create revision prompt for **carousel-copywriter**:

```
Here is Version 2 of a carousel, after one revision. Please refine it further:

[Reviewer feedback from Version 2 -- critical issues and priority fixes]

Best version so far is Version [1 or 2] with score [score]. Please aim to exceed this.

[If V1 was better, add: Note that Version 1 had superior [specific quality]. Do not lose that strength.]

Version 2 text:
[Version 2 full text]

Please return only the revised carousel, no commentary.
```

- Run **carousel-copywriter** to generate Version 3
- Run **carousel-reviewer** on Version 3
- Capture metrics

### Phase 5: Final Comparison & Selection

Compare all three versions:

| Version | Score | Swipe Power | Cut Pass | Adherence | Key Change |
|---------|-------|-------------|----------|-----------|------------|
| V1 | [score] | [score] | [pass/fail] | [score]/25 | -- |
| V2 | [score] | [score] | [pass/fail] | [score]/25 | [what improved/regressed] |
| V3 | [score] | [score] | [pass/fail] | [score]/25 | [what improved/regressed] |

**Select the version with the highest score.**

Tiebreaker:
1. Higher swipe power wins
2. Higher Sharp Cut adherence wins
3. If still tied, the later version (revisions usually preferred)
4. If still tied, return both and let user choose

### Phase 6: Generate Description

Generate **5 description options** for the winning version. Rules:
- **Max 58 characters** each (LinkedIn carousel description limit)
- Must work as a standalone hook -- the reader sees this before clicking
- Should compress the thesis, not the topic
- Vary tone across options: one thesis-driven, one data-driven, one contrast-driven, one provocative, one question
- Count characters for each and verify under 58

### Phase 6.5: AI Design Spec

After selecting the winning version, **run the `carousel-designer` skill** to produce a per-slide design spec.

1. Read the winning carousel's full text and speaker notes
2. Follow the carousel-designer skill to produce a design spec JSON
3. Save the design spec as `{carousel-slug}-design-spec.json` in the vault
4. The spec auto-generates the render command

**Key design rules:**
- No two adjacent slides with the same layout
- Max 2 diagonals per carousel
- 3-5 photos in a 10-slide carousel (mix in gradient-only slides)
- Match layout to content: HOOK -> diagonal/overlay, FRAME -> split, CORE -> full-text/split, COUNTER -> diagonal/overlay, CUT -> overlay/diagonal, QUESTION -> split/full-text

### Phase 7: Deliver Results

Return in this format:

---

## Iterative Carousel Summary

**Best Version:** Version [X]

**Final Score:** [score]/10
**Swipe Power:** [score]/10
**Sharp Cut Adherence:** [score]/25
**Final Verdict:** [Publish / Revise / Rewrite]

---

### Comparison Table

| Version | Score | Swipe Power | Cut Pass | Critical Issues | Strengths |
|---------|-------|-------------|----------|-----------------|-----------|
| V1 | [score] | [score] | [pass/fail] | [count] | [list] |
| V2 | [score] | [score] | [pass/fail] | [count] | [list] |
| V3 | [score] | [score] | [pass/fail] | [count] | [list] |

---

### Selected Carousel (Version [X])

[Best version full text with all slides and speaker notes]

### AI Design Spec

**Design spec saved to:** `{carousel-slug}-design-spec.json`

**Render command:**
```bash
python3 ~/.nexus/vault/scripts/carousel-pdf-builder.py [carousel.md] --design-spec [spec.json] --auto-bg
```

### Description Options (max 58 chars)

1. [Option 1] ([X] chars)
2. [Option 2] ([X] chars)
3. [Option 3] ([X] chars)
4. [Option 4] ([X] chars)
5. [Option 5] ([X] chars)

---

## Integration Notes

All copywriter calls MUST follow the carousel-copywriter framework:
- 10-slide structure (Hook, Frame x2, Core x3, Counter, Cut x2, Question)
- Sharp Cut style (one thesis, data-driven, short lines, tension question)
- Max 40 words per slide, max 4 lines per slide
- Output format with speaker notes

All reviewer calls MUST follow the carousel-reviewer framework:
- 5-point audit (Hook, Swipe, Data, Cut, Question)
- Sharp Cut adherence scoring (/25)
- Per-slide notes
- Calibrated scoring with action mapping

## Gotchas

- **Entry point violation:** Never call carousel-copywriter directly. All carousel requests must go through this coordinator.
- **Over-revision:** Carousels are short-form content. If a version scores 8+, ship it. Don't polish the life out of it.
- **Voice drift across revisions:** Each revision must maintain the Sharp Cut voice. If a revision starts sounding like an editorial condensed into slides, flag it.
- **Data accuracy:** If source material contains specific numbers, preserve them exactly across all revisions.
- **The counter-argument trap:** If the counter-argument slide is weak across all versions, that's a thesis problem.
- **Carousel is not editorial:** Don't let the editorial skills bleed into this workflow. Different medium, different rules, different rhythm.
