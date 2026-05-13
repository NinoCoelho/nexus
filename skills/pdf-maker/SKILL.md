---
name: pdf-maker
description: Generate designed PDFs from any structured content — reports, articles, daily digests, research summaries. Uses Pillow + fpdf2 with dual fonts, auto-fill text, and stock photo backgrounds. The generic engine that other PDF skills build on.
---

## When to use

- Generating a PDF report from a vault markdown file (tech daily, research, analysis)
- Converting any structured content into a visually designed PDF document
- When another skill (carousel-pdf-builder, etc.) needs a PDF engine under the hood
- When the user says "make a PDF of this" or "export this as a PDF"

## Architecture

```
pdf-maker.py (generic engine)
    ├── blueprint_report()    → multi-page reports with sections
    ├── blueprint_carousel()  → 10-slide LinkedIn carousel format
    ├── build_from_blueprint()→ renders pages from JSON spec
    └── save_pdf()            → assembles pages into final PDF
```

## Requirements

This skill has an isolated Python environment managed by Nexus (provides `Pillow` + `fpdf2`). After calling `skill_view(name="pdf-maker")`, use the `python.path` from the response (referred to as `$SKILL_PYTHON` below).
- Inter + Montserrat fonts at `~/.nexus/fonts/` (optional — falls back to Arial)
- Script (bundled with this skill): `~/.nexus/skills/pdf-maker/scripts/pdf-maker.py`

## Steps

### 1. From Markdown (quick)

```bash
"$SKILL_PYTHON" ~/.nexus/skills/pdf-maker/scripts/pdf-maker.py from-md <input.md> -o output.pdf [options]
```

Options:
- `--theme midnight|slate|coral|noir|clean|warm` — color palette
- `--page-size portrait|landscape|square` — default: portrait
- `--bg-image photo.jpg` — stock photo background
- `--type report|article|slides` — layout mode (default: report)

Markdown parsing: extracts `# Title`, `> subtitle`, `## Section` headers, and `- list items`. Bold items get emphasized styling.

### 2. From JSON Blueprint (full control)

```bash
"$SKILL_PYTHON" ~/.nexus/skills/pdf-maker/scripts/pdf-maker.py build blueprint.json -o output.pdf
```

**Blueprint format:**

```json
{
  "page_size": [1080, 1350],
  "margin": [70, 90],
  "theme": "midnight",
  "bg_image": "optional/global/bg.jpg",
  "pages": [
    {
      "type": "cover|body|accent",
      "bg_image": "optional/per-page/bg.jpg",
      "bg_color": [235, 75, 75],
      "label": "optional top-left label",
      "blocks": [
        {
          "role": "title_heavy|title_bold|body_regular|body_semibold|muted|...",
          "text": "Content text",
          "color": "text|accent|highlight|muted|danger|text_secondary",
          "align": "center|left",
          "size": "fill|42",
          "prefix": "→|—|•",
          "spacing": "tight|normal|loose"
        }
      ]
    }
  ]
}
```

### 3. Programmatic (from other Python scripts)

```python
import pdf_maker as pm

bp = pm.blueprint_report(
    title="My Report",
    subtitle="April 2026 Summary",
    sections=[
        {"title": "Section 1", "items": [
            {"role": "body_regular", "text": "Content here", "color": "text_secondary", "align": "left"}
        ]}
    ],
    theme="midnight"
)
pages = pm.build_from_blueprint(bp)
pm.save_pdf(pages, "output.pdf", bp["page_size"])
```

## Font Roles

| Role | Font | Used For |
|---|---|---|
| `title_heavy` | Inter ExtraBold | Main titles, hero text |
| `title_bold` | Inter Bold | Section headers |
| `title_semibold` | Inter SemiBold | Sub-sections |
| `body_extrabold` | Montserrat ExtraBold | Emphasis body |
| `body_bold` | Montserrat Bold | Strong body text |
| `body_semibold` | Montserrat SemiBold | Data points |
| `body_medium` | Montserrat Medium | Subtitles |
| `body_regular` | Montserrat Regular | Standard body |
| `muted` | Montserrat Medium | Source attribution |
| `label` | Inter SemiBold | Small labels |

## Palettes

| Theme | Style | Best For |
|---|---|---|
| `midnight` | Deep navy + electric blue | Tech, data, reports |
| `slate` | Charcoal + ice blue | SaaS, engineering docs |
| `coral` | Deep rose + coral | Bold, marketing |
| `noir` | Black + gold | Premium, editorials |
| `clean` | White + blue | Professional, print-friendly |
| `warm` | Cream + terracotta | Lifestyle, human interest |

## Text-Fit Engine

When block size is `"fill"`, the engine binary-searches for the largest font size that fills ~85% of available vertical space. Short content gets big text; dense content scales down.

## Gotchas

- **Blueprint validation is minimal** — malformed blueprints will crash. Validate JSON before passing.
- **Long sections** in `from-md` mode may need manual splitting. The parser auto-paginates by `##` headers.
- **Font fallback** — if Inter/Montserrat missing, falls back to Arial. Always verify fonts exist.
- **Stock photos are NOT fetched by this skill** — use `--bg-image` with a pre-downloaded photo from stock-media skill, or let carousel-pdf-builder handle auto-fetch.
