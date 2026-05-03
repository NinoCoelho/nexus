---
name: carousel-pdf-builder
description: Generate LinkedIn-ready carousel PDFs from Sharp Cut carousel markdown using Pillow + fpdf2. No design tools needed. Built on top of pdf-maker.
type: procedure
role: design
platform: nexus
platform_version: "0.1"
nexus_status: stable
nexus_authored_by: builtin
---

## When to use

- After creating carousel copy (via iterative-carousel-coordinator), generate the visual PDF
- Any time you have a carousel markdown file in the vault and need a LinkedIn-ready PDF
- When the user says "generate the carousel PDF" or "build the slides"

## Architecture

```
carousel-pdf-builder.py
  +-- Parses carousel markdown -> slide data
  +-- Auto-fetches stock photos (via stock-media skill)
  +-- Builds blueprint (via pdf-maker.blueprint_carousel())
  +-- Renders PDF (via pdf-maker.build_from_blueprint() + save_pdf())
```

The carousel builder is a thin layer on top of `pdf-maker.py`. All rendering, fonts, text-fitting, and PDF assembly happen in pdf-maker.

## Prerequisites

```bash
python3 -c "import PIL; import fpdf; print('OK')" || { echo "missing: pip install Pillow fpdf2"; exit 1; }
ls ~/.nexus/fonts/Inter-ExtraBold.ttf ~/.nexus/fonts/Montserrat-Regular.ttf 2>/dev/null || { echo "missing: Inter + Montserrat fonts at ~/.nexus/fonts/"; exit 1; }
ls ~/.nexus/vault/scripts/pdf-maker.py 2>/dev/null || { echo "missing: pdf-maker.py in vault scripts"; exit 1; }
```

## Steps

### 1. Verify dependencies

```bash
python3 -c "import PIL; import fpdf; print('OK')"
ls ~/.nexus/fonts/Inter-ExtraBold.ttf ~/.nexus/fonts/Montserrat-Regular.ttf
ls ~/.nexus/vault/scripts/pdf-maker.py
```

### 2. Run

```bash
python3 ~/.nexus/vault/scripts/carousel-pdf-builder.py <carousel.md> [options]
```

**Options:**
- `--style sharp|editorial|bold-slab|modern-display` -- design style pack (default: sharp)
- `--theme midnight|slate|coral|noir|clean|warm` -- color palette (default: midnight)
- `--auto-bg` -- auto-fetch stock photos per slide type via stock-media skill
- `--bg-query "corporate dark"` -- narrow auto-fetch search
- `--bg-image photo.jpg` -- single photo for ALL slides
- `--bg-image-hook photo.jpg` -- photo for hook slide only
- `--bg-image-cut photo.jpg` -- photo for cut slides only
- `--output path.pdf` -- output path

### Style Packs

| Style | Fonts | Vibe | Best For |
|-------|-------|------|----------|
| `sharp` | Inter ExtraBold + Montserrat | Clean tech, current default | Tech, data, corporate, safe choice |
| `editorial` | Playfair Display + Inter | Magazine, high-contrast serif/sans | Opinion, thought leadership, cultural commentary |
| `bold-slab` | Roboto Slab + Inter | Sturdy, newsletter | Industry analysis, instructional, newsletter |
| `modern-display` | InterDisplay Black + Montserrat Light | Ultra modern, weight contrast | Bold takes, provocative, personal brand |

**Examples:**
```bash
# Default
python3 carousel-pdf-builder.py carousel.md --auto-bg

# Magazine-style opinion piece
python3 carousel-pdf-builder.py carousel.md --style editorial --auto-bg

# Sturdy data analysis
python3 carousel-pdf-builder.py carousel.md --style bold-slab --auto-bg --theme noir
```

### 3. AI-designed carousel (recommended -- maximum visual variety)

When the coordinator produces a design spec via the `carousel-designer` skill:

```bash
python3 ~/.nexus/vault/scripts/carousel-pdf-builder.py carousel.md \
  --design-spec carousel-design-spec.json --auto-bg
```

This reads per-slide layout/style/theme/image decisions from the spec JSON. Each slide can have a different layout, style pack, and theme.

### 4. Verify

```bash
open <output.pdf>
```

## What pdf-maker Provides (shared with other skills)

- Dual-font system (Inter titles + Montserrat body)
- Text-fit engine (auto-scales fonts to fill space)
- 6 color palettes
- Gradient and photo backgrounds
- Blueprint-based rendering (JSON spec -> PDF pages)

## What carousel-pdf-builder Adds

- Carousel markdown parser (stops at Speaker Notes)
- Slide type classification (HOOK, FRAME, CORE, COUNTER, CUT, QUESTION)
- Per-slide-type stock photo queries
- CUT slide accent detection
- COUNTER label ("THE OTHER SIDE")
- Auto-fetch integration with stock-media skill
- Design spec support (`--design-spec`) -- per-slide layout/style/theme/image

## Gotchas

- **Parser stops at `## Speaker Notes` (any heading level h1-h6)** -- visual direction notes won't render as content.
- **Auto-bg can fail** if stock APIs are rate-limited -- falls back to gradient
- **Must be in same directory as pdf-maker.py** -- imports it as a module
