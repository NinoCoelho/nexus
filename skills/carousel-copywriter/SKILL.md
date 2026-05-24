---
name: carousel-copywriter
description: Generate LinkedIn carousel slide copy using the Sharp Cut style. Called by iterative-carousel-coordinator during creation and revision cycles -- do not call directly.
type: procedure
role: content
platform: nexus
platform_version: "0.1"
nexus_status: stable
nexus_authored_by: builtin
---

## When to use

- **ONLY when called by iterative-carousel-coordinator** during a carousel creation or revision cycle
- Never invoke directly for user carousel requests -- use iterative-carousel-coordinator instead
- This skill is the creative engine within that workflow, not the entry point

## The Sharp Cut Voice

Carousels written here follow the **Sharp Cut** style:

- **One thesis, no detours.** Every slide serves the argument or gets cut.
- **Data as dialogue.** Numbers aren't decoration -- they're the argument itself.
- **Short lines, real weight.** Each line carries meaning. No filler lines, no drama breaks.
- **The reader finishes in 60 seconds** and has something specific to respond to.
- **Ends with a tension question**, not a gentle CTA.

## Carousel Structure (8-10 slides)

Every carousel MUST follow this architecture:

### Slide 1: The Hook
- One bold line + a subtitle promise
- Must land before the "See more" cutoff if posted as text (~210 chars)
- Pattern: `[Provocative statement.]` / `[What this carousel delivers]`
- NO greeting, NO "I analyzed X posts," NO throat-clearing

### Slides 2-3: The Frame
- Set up the comparison, contradiction, or context
- Hard data only -- percentages, dollar amounts, headcounts
- One data point per slide max
- Source attribution in small text when available

### Slides 4-6: The Core
- The argument itself -- the comparison, the pattern, the structural insight
- Each slide = one idea, one line of reasoning
- Use contrast formatting: Company A does X. / Company B does Y.
- No analysis longer than 3 lines per slide

### Slide 7: The Counter-Argument (or Twist)
- Present the strongest opposing view honestly
- This is what makes the piece credible -- without it, it's propaganda
- Must be genuinely defensible, not a straw man

### Slide 8-9: The Cut
- The sharp observation that reframes everything
- Where the thesis crystallizes into one devastating line
- This is the slide people screenshot and share

### Slide 10: The Question
- One specific, debatable, impossible-to-ignore question
- NOT "What do you think?" -- that's a dead CTA
- Instead: a question that forces the reader to take a position
- Include a subtle call-to-follow or share if appropriate

## Writing Rules

### Per-Slide Rules
- **Max 40 words per slide** (excluding source attribution)
- **Max 4 lines per slide**
- **One idea per slide** -- if you have two ideas, you need two slides
- **No slide should feel like filler** -- every slide earns its swipe

### Formatting
- Use `/` to separate the main line from the subtext within a slide
- Use `->` for data points or consequences
- Use `--` for attribution or source notes
- Use `**bold**` sparingly -- only for the one phrase that must land

### Banned Patterns
- "Let that sink in"
- "Think about it"
- "The reality is"
- "Here's the thing"
- "Food for thought"
- Any rhetorical question that isn't the final slide
- Emojis as decoration (only as data labels)
- "Comment below" / "Drop your thoughts"
- Padding a weak point across multiple slides

### What to Write INSTEAD
- Specific numbers: "16 weeks" not "generous severance"
- Named contrasts: "Oracle: 4 weeks. Meta: 16 weeks."
- Active verbs: "Oracle fired. Meta notified."
- Implication over explanation: let the data gap speak
- One sharp sentence where others would write three

## Steps

1. **Receive the brief** -- topic, source material, angle, any revision notes from the coordinator
2. **Identify the thesis** -- one sentence that is the entire argument. If you can't state it in one sentence, the carousel isn't ready.
3. **Map the data** -- list every hard number available, assign each to a slide
4. **Find the counter-argument** -- what would the other side say? Make it strong.
5. **Find the cut** -- the one line that reframes the whole thing. This goes on slide 8-9.
6. **Draft all slides** -- follow the structure above. Write the hook last (it's easier once you know where the piece lands)
7. **Cut ruthlessly** -- remove any slide that doesn't advance the thesis. Remove any line that rephrases a previous line. Remove any word that doesn't carry weight.
8. **Write speaker notes** -- for each slide, add a brief note on visual direction (what the design should emphasize, layout suggestions). Keep to one sentence per slide.
9. **Final check** -- read all slide text in sequence. Does it flow? Does each slide make you want to swipe? Does the last slide make you want to comment?

## Output Format

Return the carousel in this exact format:

```
## Carousel: [Title]

**Thesis:** [One sentence]

---

### Slide 1 -- HOOK
[Main line]
[Subtext/promise]

### Slide 2 -- FRAME
[Content]

### Slide 3 -- FRAME
[Content]

### Slide 4 -- CORE
[Content]

### Slide 5 -- CORE
[Content]

### Slide 6 -- CORE
[Content]

### Slide 7 -- COUNTER
[Content]

### Slide 8 -- CUT
[Content]

### Slide 9 -- CUT
[Content]

### Slide 10 -- QUESTION
[Tension question]
[CTA if appropriate]

---

**Word count:** [total words across all slides]
**Speaker notes:**
- Slide 1: [visual direction]
- Slide 2: [visual direction]
- [... etc]
```

## Gotchas

- **Too many slides:** If the argument needs more than 10 slides, the thesis isn't sharp enough. Narrow it.
- **Data dump slides:** Never stack 3+ stats on one slide. One per slide, let it breathe.
- **Weak counter-argument:** If the counter is obviously wrong, the piece feels manipulative. Make it genuinely strong.
- **Missing the cut:** If slide 8-9 doesn't reframe the entire argument, the carousel is just information, not insight.
- **Soft question:** If the final question doesn't force a position, it's not a Sharp Cut ending.
- **Voice drift:** Don't slip into editorial voice. This is tighter, punchier, more direct. Think billboard, not essay.
