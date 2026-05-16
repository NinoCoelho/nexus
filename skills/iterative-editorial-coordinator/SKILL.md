---
name: iterative-editorial-coordinator
description: THE entry point for all editorial work. Use this whenever creating or improving editorials. Coordinates article-editor and editorial-ghostwriter through 3 iterations with quality scoring, returning the best version. Never call editorial-ghostwriter directly.
type: procedure
role: editorial
platform: nexus
platform_version: "0.1"
nexus_status: stable
nexus_authored_by: builtin
---

## When to use

- **ALWAYS when a user asks for an editorial** -- this is the only skill that should handle editorial requests
- Creating editorials from scratch (from news, topics, or source material)
- Improving an existing editorial draft through multiple editorial passes
- When a single edit isn't enough -- need iterative refinement with quality scoring
- Coordinating article-editor feedback with editorial-ghostwriter revisions
- Any time you want "try this, review, try again, pick the best" for opinion/analysis pieces
- **DO NOT call editorial-ghostwriter directly** -- it is an internal skill called only by this coordinator

## Entry Point Rule

**Editorial-ghostwriter is INTERNAL ONLY.**

- Users ask for editorials -> You call **iterative-editorial-coordinator**
- Coordinator handles the full workflow internally:
  - Creates initial draft using editorial-ghostwriter (if needed)
  - Runs article-editor assessments
  - Generates revisions through editorial-ghostwriter
  - Compares versions, selects winner, returns results
- You never need to call editorial-ghostwriter directly

This ensures every editorial goes through quality review and iterative improvement.

## Steps

### Phase 0: Creation (if starting from topic, not draft)

If the user provides a topic/news/source but no draft:

1. **Run editorial-ghostwriter** to create the initial draft
   - Provide the source material (news summary, topic notes, facts)
   - Use the full editorial-ghostwriter framework (7-part structure, two-level analysis)
   - Generate a complete editorial as Version 1

2. **Proceed to Phase 1 with Version 1 as the initial text**

If the user provides an existing draft, **skip Phase 0 and start at Phase 1.**

### Phase 1: Initial Assessment

- Run **article-editor** on the current text (Version 1)
- Capture:
  - Overall verdict (Publish/Needs work/Reject)
  - Critical issues list
  - Quality score (use rubric below)
  - Strengths to preserve

Store as **Version 1** with metadata.

### Phase 2: First Revision

- Create revision prompt for **editorial-ghostwriter**:
  ```
  Here is a piece of text that needs revision. Please rewrite it addressing these specific editorial issues:
  
  [Editor feedback from Version 1]
  
  Preserve these strengths:
  [Strengths from Version 1]
  
  Original text:
  [Version 1 text]
  
  Please return only the revised text, no commentary.
  ```

- Run **editorial-ghostwriter** to generate Version 2
  - Ensure ghostwriter applies its full framework: fact+displacement, context, thesis, counter-argument, Level 1 analysis, Level 2 expansion, closing
  - Maintain editorial voice: authoritative, analytical, no simplification
- Run **article-editor** on Version 2
- Capture same metrics as Version 1

### Phase 3: First Comparison & Decision

Compare Version 1 and Version 2:

| Version | Quality Score | Verdict |
|---------|---------------|---------|
| V1 (original) | [score] | [verdict] |
| V2 (first revision) | [score] | [verdict] |

**If V2 score > V1 score:**
- Proceed to next revision with V2 as base
- Note improvements made

**If V2 score <= V1 score:**
- Flag the regression
- Note that V1 is currently best
- Proceed anyway (the next pass might recover)
- Explicitly tell ghostwriter in next prompt that V1 was stronger

### Phase 4: Second Revision

Create revision prompt for **editorial-ghostwriter**:
```
Here is Version 2 of a piece, after one editorial revision. Please refine it further addressing these issues:

[Editor feedback from Version 2]

Best version so far is Version [1 or 2] with score [score]. Please aim to exceed this.

[If V1 was better, add: Note that Version 1 had superior [specific quality]. Do not lose that strength.]

Version 2 text:
[Version 2 text]

Please return only the revised text, no commentary.
```

- Run **editorial-ghostwriter** to generate Version 3
- Run **article-editor** on Version 3
- Capture metrics

### Phase 5: Final Comparison & Selection

Compare all three versions:

| Version | Quality Score | Verdict | Key Change |
|---------|---------------|---------|------------|
| V1 (original/created) | [score] | [verdict] | -- |
| V2 (first revision) | [score] | [verdict] | [what improved/regressed] |
| V3 (second revision) | [score] | [verdict] | [what improved/regressed] |

**Select the version with the highest score.**

If scores are tied, select:
1. The version with fewer critical issues
2. If still tied, the later version (revisions usually preferred)
3. If still tied, return both and let user choose

### Phase 6: Deliver Results

Return in this format:

---

## Iterative Editorial Summary

**Best Version:** Version [X]

**Final Quality Score:** [score]/10
**Final Verdict:** [Publish/Needs work/Reject]

---

### Comparison Table

| Version | Score | Critical Issues | Strengths |
|---------|-------|-----------------|-----------|
| V1 | [score] | [count] | [list] |
| V2 | [score] | [count] | [list] |
| V3 | [score] | [count] | [list] |

---

### Selected Text (Version [X])

[Best version text]

---

### Journey Summary

**V1 -> V2:** [what changed, what improved, what regressed]

**V2 -> V3:** [what changed, what improved, what regressed]

**Key insight:** [what the process revealed about the piece]

---

### Editor's Final Notes on Best Version

[Full editorial feedback on the winning version, including remaining issues if any]

## Editorial-Ghostwriter Integration

All ghostwriter calls in this process MUST follow the editorial-ghostwriter framework:

1. **Structure:** 7-part framework (Opening with fact+displacement, Context, Thesis, Counter-argument, Level 1 analysis, Level 2 expansion, Closing)
2. **Voice:** Authoritative, analytical, institutional insight, no simplification
3. **Two-level analysis:** Must reveal structural patterns, not just surface observations
4. **Stylistic principles:** Alternate long/short sentences, vary articulators, mark precision when necessary
5. **Subtext rule:** Don't explain everything -- let intelligent readers perceive
6. **Language:** Always English, regardless of input language

The ghostwriter is not a general copywriter -- it is specifically for substantive analysis pieces where the goal is clarity, depth, and discernment.

## Gotchas

- **Entry point violation:** Never call editorial-ghostwriter directly. Users asking for editorials must go through this coordinator to ensure quality review
- **Quality score consistency:** Use the rubric below rigorously across all versions
- **Ghostwriter regression:** Sometimes revisions can over-edit and lose voice. If V2 is worse than V1, explicitly tell ghostwriter in V3 prompt that earlier version was better
- **Infinite refinement risk:** Stop at 3 iterations. More usually yields diminishing returns
- **Genre mismatch:** Ensure editor evaluates as an editorial/opinion piece, not news or feature
- **Preserve original strengths:** Always pass strengths through to ghostwriter so they're not edited out
- **Two-level analysis requirement:** If any version lacks the Level 2 expansion (structural/institutional insight), that's a critical issue that must be flagged

## Quality Score Rubric (Assign consistently)

| Score | Description | Typical Issues |
|-------|-------------|----------------|
| 10 | Publish-ready | None or trivial |
| 9 | Near-perfect | 1-2 minor polish points |
| 8 | Strong | Minor structural or stylistic issues |
| 7 | Good | A few issues but solid overall |
| 6 | Fair | Several issues, needs revision |
| 5 | Marginal | Functional but weak in multiple areas |
| 4 | Poor | Significant structural or substantive problems |
| 3 | Very poor | Major failures, needs complete rethink |
| 2 | Failing | Multiple fundamental problems |
| 1-0 | Unsalvageable | Core concept broken |

**Use this rubric to assign scores consistently across all three versions.**
