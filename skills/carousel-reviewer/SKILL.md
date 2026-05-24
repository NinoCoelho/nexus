---
name: carousel-reviewer
description: Evaluate LinkedIn carousel copy for quality, swipe-power, and Sharp Cut adherence. Called by iterative-carousel-coordinator during revision cycles -- do not call directly.
type: procedure
role: editorial
platform: nexus
platform_version: "0.1"
nexus_status: stable
nexus_authored_by: builtin
---

## When to use

- **ONLY when called by iterative-carousel-coordinator** during a review cycle
- Never invoke directly -- the coordinator manages the workflow
- This skill is the quality gate within that workflow

## Review Framework

Every review produces:
1. **Overall score** (1-10)
2. **Swipe power rating** (how many readers will reach the last slide)
3. **Critical issues** (must fix)
4. **Strengths** (must preserve in revision)
5. **Per-slide notes** (quick hits)

## The 5-Point Audit

### 1. Hook Power (Pass/Fail)
**The "See more" test:** Would you expand this post in a crowded feed?

| Signal | Pass | Fail |
|---|---|---|
| Opens with tension, contrast, or a number | Yes | No |
| Promise is specific ("X vs Y in 10 slides") not vague ("Thoughts on layoffs") | Yes | No |
| Under 210 characters | Yes | No |
| No throat-clearing ("I've been thinking about..." / "Hot take:") | Yes | No |

If hook fails -> **score capped at 5**

### 2. Swipe Momentum (1-10)
Read all slides in sequence. Rate the compulsion to swipe.

- Does each slide create a question answered by the next?
- Is there a single slide you'd consider skipping?
- Does the pace accelerate toward the end?
- Score: 10 = cannot stop swiping, 5 = reading out of duty, 1 = abandoned by slide 4

### 3. Data Discipline (Pass/Fail)
- One data point per slide max
- Every number is specific (not "generous" but "16 weeks")
- Sources attributed where possible
- No decorative statistics (numbers that don't serve the thesis)

### 4. The Cut Test (Pass/Fail)
Read slides 8-9 in isolation. Do they:
- Reframe the entire argument in a new light?
- Contain at least one line a reader would screenshot?
- Feel earned by everything that came before?

If the cut doesn't land -> **score capped at 7**

### 5. Question Strength (1-10)
The final slide question must:
- Force the reader to take a position (not just "reflect")
- Be specific enough to generate a 15+ word comment
- Not be answerable with "it depends" or "both"
- Connect to the thesis, not just the topic

## Per-Slide Checks

For each slide, flag:
- **Over 40 words** -> trim
- **More than one idea** -> split
- **Filler lines** (anything that rephrases a previous slide) -> cut
- **Banned phrases** (see carousel-copywriter skill) -> replace
- **Missing visual direction** in speaker notes -> request

## Sharp Cut Adherence Score

Rate how well the piece follows the Sharp Cut principles:

| Principle | Score (1-5) | Notes |
|---|---|---|
| One thesis, no detours | -- | |
| Data as dialogue | -- | |
| Short lines, real weight | -- | |
| 60-second finish | -- | |
| Tension question ending | -- | |

**Total: /25**

## Output Format

Return the review in this exact format:

```
## Carousel Review

**Overall Score:** [X]/10
**Swipe Power:** [X]/10
**Cut Strength:** Pass/Fail
**Hook Pass:** Yes/No
**Sharp Cut Adherence:** [XX]/25

---

### Critical Issues
1. **[Category]** -- [Specific issue] -- [Which slide(s)] -- [How to fix]

### Strengths to Preserve
- [What works and why]

### Per-Slide Notes
- Slide 1: [verdict in 5 words]
- Slide 2: [verdict in 5 words]
- [... etc]

### Swipe Momentum Analysis
[2-3 sentences on pacing: where it accelerates, where it drags]

### Verdict
[Publish / Revise / Rewrite]

### If Revising, Priority Fixes
1. [Most important fix]
2. [Second most important]
3. [Third most important]
```

## Scoring Calibration

| Score | Meaning | Action |
|---|---|---|
| 9-10 | Publish immediately | Ship it |
| 7-8 | Strong, minor polish | 1 revision pass |
| 5-6 | Functional but flat | Major revision |
| 3-4 | Structural problems | Consider rewrite |
| 1-2 | Fundamental failure | Rewrite from scratch |

## Gotchas

- **Don't confuse style with substance:** A punchy carousel with a weak thesis scores lower than a slightly wordy one with a strong thesis
- **The counter-argument check:** If slide 7 is a straw man, the whole piece loses credibility. Flag it.
- **Carousel is not editorial:** Don't review this like an article. The medium is different. Swipe momentum matters more than paragraph depth.
- **Over-polishing risk:** Sometimes revision kills the energy. If a piece scores 8+ and has only minor issues, recommend publishing rather than risking over-edit.
