---
name: rich-pptx-design
description: Create visually rich, professionally designed PowerPoint presentations that look crafted by a top-tier design agency. Use this whenever you need to create, edit, or redesign a .pptx file -- pitch decks, slide decks, presentations, or any slide-based output. Prefer over generic slide creation for cases where visual impact matters.
type: procedure
role: design
platform: nexus
platform_version: "0.1"
nexus_status: stable
nexus_authored_by: builtin
---

# Rich PowerPoint Design

## When to use

- Creating any new PowerPoint deck from scratch
- Redesigning or polishing an existing .pptx file
- Converting content (notes, outlines, reports) into a slide deck
- Any task where a `.pptx` file is the deliverable and visual quality matters

## Prerequisites

```bash
command -v node >/dev/null || { echo "missing: node -- install with: brew install node (macOS) | apt-get install nodejs (Debian/Ubuntu)"; exit 1; }
npm list -g pptxgenjs >/dev/null 2>&1 || { echo "missing: pptxgenjs -- install with: npm install -g pptxgenjs"; exit 1; }
command -v soffice >/dev/null || { echo "missing: LibreOffice (soffice) -- install with: brew install --cask libreoffice (macOS) | apt-get install libreoffice (Debian/Ubuntu)"; exit 1; }
```

## Design Thinking -- Before You Build

Before writing any slide code, commit to a bold aesthetic direction:

1. **Purpose** -- What story does this deck tell? Who's in the room?
2. **Tone** -- Pick a deliberate aesthetic: midnight executive, coral energy, brutalist minimal, editorial magazine, forest organic, art deco geometric. Not "professional blue."
3. **Differentiation** -- What's the one thing someone will remember about this deck?
4. **Constraints** -- Template lock-in? Brand colors? Max slides?

Match complexity to ambition. A maximalist keynote needs elaborate builds per slide; a refined investor deck needs restraint and precision.

---

## Reading Existing Files

```bash
# Extract text content
python -m markitdown presentation.pptx

# Visual thumbnail grid
python scripts/thumbnail.py presentation.pptx

# Unpack for XML-level editing
python scripts/office/unpack.py presentation.pptx unpacked/
```

---

## Creating from Scratch

Use **pptxgenjs** (Node.js) when no template exists.

## Editing Existing Templates

1. Analyze template with `thumbnail.py`
2. Unpack -> manipulate slide XML -> edit content -> clean -> pack

---

## Color Palettes

Never default to generic blue. Pick colors that match the topic -- each deck deserves its own palette. One color should dominate (60-70% visual weight), 1-2 supporting tones, one sharp accent.

| Theme | Primary | Secondary | Accent |
|-------|---------|-----------|--------|
| **Midnight Executive** | `1E2761` (navy) | `CADCFC` (ice blue) | `FFFFFF` (white) |
| **Forest & Moss** | `2C5F2D` (forest) | `97BC62` (moss) | `F5F5F5` (cream) |
| **Coral Energy** | `F96167` (coral) | `F9E795` (gold) | `2F3C7E` (navy) |
| **Warm Terracotta** | `B85042` (terracotta) | `E7E8D1` (sand) | `A7BEAE` (sage) |
| **Ocean Gradient** | `065A82` (deep blue) | `1C7293` (teal) | `21295C` (midnight) |
| **Charcoal Minimal** | `36454F` (charcoal) | `F2F2F2` (off-white) | `212121` (black) |
| **Teal Trust** | `028090` (teal) | `00A896` (seafoam) | `02C39A` (mint) |
| **Berry & Cream** | `6D2E46` (berry) | `A26769` (dusty rose) | `ECE2D0` (cream) |
| **Sage Calm** | `84B59F` (sage) | `69A297` (eucalyptus) | `50808E` (slate) |
| **Cherry Bold** | `990011` (cherry) | `FCF6F5` (off-white) | `2F3C7E` (navy) |

**Structure tip:** Dark backgrounds for title + conclusion slides, light for content ("sandwich" structure). Or commit to dark throughout for premium feel.

---

## Typography

Choose an interesting font pairing -- never default to Arial alone.

| Header Font | Body Font |
|-------------|-----------|
| Georgia | Calibri |
| Arial Black | Arial |
| Calibri | Calibri Light |
| Cambria | Calibri |
| Trebuchet MS | Calibri |
| Impact | Arial |
| Palatino | Garamond |
| Consolas | Calibri |

| Element | Size |
|---------|------|
| Slide title | 36-44pt bold |
| Section header | 20-24pt bold |
| Body text | 14-16pt |
| Captions | 10-12pt muted |

Avoid generic fonts (Inter, Roboto, system defaults). Pair a distinctive display font with a clean body font.

---

## Layout & Composition

### Every slide needs a visual element

Text-only slides are forgettable. Add images, charts, icons, shapes, or diagrams.

**Layout patterns to rotate through:**

- **Two-column** -- text left, illustration right
- **Icon + text rows** -- icon in colored circle, bold header, description below
- **2x2 or 2x3 grid** -- image on one side, grid of content blocks on the other
- **Half-bleed image** -- full left or right side with content overlay
- **Large stat callouts** -- big numbers 60-72pt with small labels below
- **Comparison columns** -- before/after, pros/cons, side-by-side options
- **Timeline / process flow** -- numbered steps, arrows

### Spacing

- 0.5" minimum margins from slide edges
- 0.3-0.5" between content blocks
- Leave breathing room -- don't fill every inch
- Use consistent gap sizes (pick 0.3" or 0.5" and stick with it)

### Visual Motif

Pick ONE distinctive element and repeat it across every slide -- rounded image frames, icons in colored circles, thick single-side borders, geometric corner accents. Carry it throughout.

---

## Backgrounds & Visual Depth

Don't default to solid white. Build atmosphere:

- Gradient fills (linear or radial, subtle two-tone)
- Noise textures and grain overlays
- Geometric patterns as watermark layers
- Layered transparencies with semi-transparent shapes
- Dramatic shadows on content cards
- Decorative border treatments

Match depth to tone: executive decks need subtle texture; pitch decks can go bolder.

---

## Anti-Patterns -- Avoid These

- **Don't repeat the same layout** -- vary columns, cards, and callouts across slides
- **Don't center body text** -- left-align paragraphs and lists; center only titles
- **Don't skimp on size contrast** -- titles need 36pt+ to stand out from 14-16pt body
- **Don't default to blue** -- pick colors that reflect the specific topic
- **Don't create text-only slides** -- add images, icons, charts, or visual elements
- **Don't forget text box padding** -- set `margin: 0` on text boxes or offset shapes to account for padding
- **Don't use low-contrast elements** -- icons AND text need strong contrast against backgrounds
- **NEVER use accent lines under titles** -- hallmark of AI-generated slides; use whitespace or background color instead
- **Don't style one slide and leave the rest plain** -- commit fully or keep it simple throughout
- **Don't mix spacing randomly** -- choose one rhythm and hold it
- **Don't skimp on image resolution** -- 150 DPI minimum for print, 72 DPI for screen
- **Don't overuse transitions/animations** -- one well-chosen transition style beats a random mix

---

## QA -- Required Before Delivery

Assume there are problems. Your first render is almost never correct.

### Content QA

```bash
python -m markitdown output.pptx
```

Check for missing content, typos, wrong order.

**Check for leftover placeholders:**

```bash
python -m markitdown output.pptx | grep -iE "xxxx|lorem|ipsum|this.*(page|slide).*layout"
```

### Visual QA

Convert to images:

```bash
python scripts/office/soffice.py --headless --convert-to pdf output.pptx
pdftoppm -jpeg -r 150 output.pdf slide
```

Then inspect with a visual QA prompt covering overlapping elements, text overflow, low contrast, uneven gaps, alignment issues, and placeholder remnants.

### Verification Loop

1. Generate -> Convert to images -> Inspect
2. List issues found (if none, look again more critically)
3. Fix issues
4. Re-verify affected slides -- one fix often creates another problem
5. Repeat until a full pass reveals no new issues

**Do not declare success until at least one fix-and-verify cycle completes.**

---

## Gotchas

- **Font availability** -- not all fonts are available on all systems. Verify the target system has your chosen fonts, or embed them.
- **Slide transitions and animations** -- use sparingly. One well-chosen transition style beats a random mix.
- **Image resolution** -- use high-res images (150 DPI minimum for print, 72 DPI for screen).
- **Color consistency** -- apply the same palette rules to charts and data visualizations.
- **Text box padding** -- when aligning decorative shapes to text edges, account for default text box padding.

## Dependencies

- `pip install "markitdown[pptx]"` -- text extraction
- `pip install Pillow` -- thumbnail grids
- `npm install -g pptxgenjs` -- creating from scratch
- LibreOffice (`soffice`) -- PDF conversion
- Poppler (`pdftoppm`) -- PDF to images
