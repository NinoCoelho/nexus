---
name: carousel-designer
description: Use this after iterative-carousel-coordinator produces winning carousel copy to generate a per-slide design spec with layout, style, theme, and stock image choices. Prefer over manual style selection for visual variety.
type: procedure
role: design
platform: nexus
platform_version: "0.1"
nexus_status: stable
nexus_authored_by: builtin
---

## When to use
- After `iterative-carousel-coordinator` produces the winning carousel copy
- Before running `carousel-pdf-builder`
- Any time the user wants AI-driven creative design decisions for a carousel
- When the user says "design it", "make it look good", "render it"

## What this does

Reads the carousel copy and produces a **design spec JSON** -- one layout/style/theme/image decision per slide. The spec feeds into `carousel-pdf-builder --design-spec`.

## Slide Canvas

| Platform | Size (px) | Aspect Ratio |
|----------|-----------|--------------|
| LinkedIn carousel | 1080 x 1350 | 4:5 portrait |
| Instagram portrait | 1080 x 1350 | 4:5 portrait |
| Instagram square | 1080 x 1080 | 1:1 |

**Safe zone:** Keep critical text and graphics at least 60px from all edges.

## Design Toolbox

### Layout Modes (pick one per slide)
| Layout | Description | Best For |
|--------|-------------|----------|
| `full-text` | Gradient/photo bg + centered/left text blocks | Data slides, clean content |
| `split-vertical` | Image left + text panel right (or reversed) | Context + data, FRAME slides |
| `split-horizontal` | Image top + text panel bottom | Impact image + explanation |
| `overlay-panel` | Full photo + floating semi-transparent text box | Emotional beats, CUT/COUNTER |
| `diagonal-right` | Diagonal polygon overlay bottom-left -> top-right | Dramatic openings, HOOK |
| `diagonal-left` | Diagonal polygon overlay top-right -> bottom-left | Visual contrast, alternate diagonal |
| `image-only` | Full-bleed photo, no text | Visual break (use sparingly) |

### Style Packs (can mix per slide)
| Style | Fonts | Vibe |
|-------|-------|------|
| `sharp` | Inter + Montserrat | Clean tech (default) |
| `editorial` | Playfair Display + Inter | Magazine, serif/sans |
| `bold-slab` | Roboto Slab + Inter | Sturdy, newsletter |
| `modern-display` | InterDisplay + Montserrat Light | Ultra modern |

### Themes (implemented in pdf-builder)
`midnight` (default), `slate`, `coral`, `noir`, `clean`, `warm`

### Color Palettes

Don't default to generic blue. Pick colors that reflect the specific topic. One color should dominate (60-70% visual weight), with 1-2 supporting tones and one sharp accent.

**Core themes (mapped in pdf-builder):**
| Theme | Primary | Secondary | Accent |
|-------|---------|-----------|--------|
| **Midnight Executive** | `#1E2761` navy | `#CADCFC` ice blue | `#FFFFFF` white |
| **Slate** | `#36454F` charcoal | `#F2F2F2` off-white | `#212121` black |
| **Coral Energy** | `#F96167` coral | `#F9E795` gold | `#2F3C7E` navy |
| **Noir** | `#212121` black | `#363636` dark grey | `#FFFFFF` white |
| **Clean** | `#FFFFFF` white | `#F5F5F5` light grey | `#1E2761` navy |
| **Warm** | `#B85042` terracotta | `#E7E8D1` sand | `#A7BEAE` sage |

### Typography Size Scale (1080px wide canvas)

| Element | Size | Weight |
|---------|------|--------|
| Cover title | 64-80pt | Bold |
| Slide headline | 44-56pt | Bold |
| Body text | 28-36pt | Regular |
| Captions / labels | 20-26pt | Regular or Italic |
| Stats / callout numbers | 80-120pt | Bold or Black |

**Key rule:** Headlines need 44pt+ to stand out from body text. Size contrast is the single biggest quality lever.

### Text Guidelines
- **Left-align body text** -- center only titles and single-line callouts
- **Max 6-8 words per line** -- wider lines lose readability on mobile
- **Max 40 words per slide** -- white space > more text
- **Line height 1.3-1.5x** for body, tighter (1.1-1.2x) for headlines

## Design Principles

1. **No two adjacent slides with the same layout** -- always vary
2. **Max 2 diagonals per carousel** -- powerful but repetitive if overused
3. **Vary image density** -- mix gradient-only slides with photo slides (3-5 photos in a 10-slide carousel)
4. **Theme shifts for emotional beats** -- COUNTER can go warm/grey, CUT can hit coral/red, QUESTION returns to clean
5. **Match layout to content**:
   - HOOK: `diagonal-right` or `overlay-panel` (dramatic opening)
   - FRAME with data: `split-vertical` (image + text side by side)
   - CORE with comparison: `full-text` (clean data, no distraction)
   - CORE with narrative: `split-horizontal` or `overlay-panel`
   - COUNTER: `overlay-panel` or `diagonal-left` (visual contrast from rest)
   - CUT: `overlay-panel` or `diagonal-right` (emotional image + thesis)
   - QUESTION: `split-vertical` or `full-text` (clean, forward-looking)
6. **Stock queries must be specific to each slide's actual content** -- not generic per-type
7. **Style can vary per slide** but don't overdo it -- 2 styles max, pick a dominant and an accent
8. **Pick a bold, content-informed color palette**
9. **Define a visual motif** -- Pick ONE distinctive element and repeat it
10. **Plan the visual arc** -- Dark cover -> light content slides -> dark CTA (sandwich structure), or commit to one tone throughout

## Steps

1. **Read the carousel markdown** -- parse all slides, their types, and content
2. **Analyze the narrative arc** -- identify emotional peaks, data moments, contrast points
3. **Pick a color palette** -- choose from core themes or extended palettes based on topic relevance
4. **Assign layouts** following the principles above -- vary aggressively
5. **Pick styles and themes** -- choose a dominant style for ~70% of slides, use a second style for accent slides
6. **Write stock image queries** -- specific to each slide's content, mood, and visual direction
7. **Set layout_params** -- tune ratios, alpha, positions to match the content density
8. **Output the design spec JSON** -- save as `{carousel-name}-design-spec.json` alongside the carousel markdown
9. **Run QA checklist** (see below) before finalizing

## Output Format

```json
{
  "carousel": "slug-name",
  "dominant_style": "editorial",
  "accent_style": "bold-slab",
  "dominant_theme": "midnight",
  "slides": [
    {
      "slide": 1,
      "type": "HOOK",
      "layout": "diagonal-right",
      "style": "editorial",
      "theme": "noir",
      "image_query": "specific descriptive query for stock photo",
      "layout_params": {
        "angle": 30,
        "overlay_alpha": 0.82
      },
      "designer_notes": "Why this layout/style/image serves this slide"
    }
  ]
}
```

## Visual QA Checklist

Before finalizing any carousel design spec, verify:

- [ ] **Cover hook** -- Does slide 1 stop the scroll? Bold text, strong contrast?
- [ ] **Color consistency** -- Same palette across all slides? No accidental color drift?
- [ ] **Typography consistency** -- Same font pairing, same size scale on every slide?
- [ ] **Visual motif** -- One repeated element present across slides?
- [ ] **No consecutive identical layouts** -- Vary columns, grids, callouts
- [ ] **Text readability** -- Sufficient contrast between text and background?
- [ ] **Safe zone** -- No critical content within 60px of edges?
- [ ] **Spacing** -- Consistent gaps (30px or 50px) throughout?
- [ ] **No accent lines under titles** -- Hallmark of AI-generated slides; use whitespace or color instead
- [ ] **CTA slide** -- Contrasts with content slides, includes follow/handle/action

## Integration with carousel-pdf-builder

After generating the spec, render with:
```bash
python3 ~/.nexus/vault/scripts/carousel-pdf-builder.py carousel.md --design-spec spec.json --auto-bg
```

## Gotchas
- **Diagonal text overflow:** If a slide has many lines (>5), avoid diagonal -- text may not fit. Use `overlay-panel` or `full-text` instead.
- **Split ratio too extreme:** Below 0.35 the text panel becomes too narrow for readable text. Keep between 0.4-0.65.
- **Image-only with no photos:** The `image-only` layout requires `image_query`. If stock fetch fails, it'll be a blank slide -- use sparingly.
- **Over-reliance on one layout:** Diagonals and overlays feel premium but lose impact if every slide uses them. Mix in `full-text` and `split` slides.
- **Palette-topic mismatch:** A coral palette for a funeral industry carousel is jarring. Match tone to topic.
