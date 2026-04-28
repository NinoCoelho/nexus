#!/usr/bin/env python3
"""
PDF Maker — Generic PDF generator from Markdown/structured content.
Pillow for page rendering + fpdf2 for PDF assembly.
Supports multiple document types, dual fonts, stock photo backgrounds.

Usage:
  # From a JSON blueprint (programmatic)
  python3 pdf-maker.py build blueprint.json -o output.pdf

  # From markdown (auto-detected type)
  python3 pdf-maker.py from-md input.md -o output.pdf --type report
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
from fpdf import FPDF

# ─── CONFIG ────────────────────────────────────────────────────

FONT_DIR = os.path.expanduser("~/.nexus/fonts")
DPI = 150

# ─── FONTS ─────────────────────────────────────────────────────

FONT_ROLES = {
    "title_heavy":    "Inter-ExtraBold.ttf",
    "title_bold":     "Inter-Bold.ttf",
    "title_semibold": "Inter-SemiBold.ttf",
    "title_medium":   "Inter-Medium.ttf",
    "body_regular":   "Montserrat-Regular.ttf",
    "body_medium":    "Montserrat-Medium.ttf",
    "body_semibold":  "Montserrat-SemiBold.ttf",
    "body_bold":      "Montserrat-Bold.ttf",
    "body_extrabold": "Montserrat-ExtraBold.ttf",
    "label":          "Inter-SemiBold.ttf",
    "muted":          "Montserrat-Medium.ttf",
    "mono":           "Inter-Regular.ttf",
}

_font_cache = {}

def get_font(role, size):
    key = (role, size)
    if key not in _font_cache:
        fname = FONT_ROLES.get(role, "Montserrat-Regular.ttf")
        fpath = os.path.join(FONT_DIR, fname)
        if os.path.exists(fpath):
            _font_cache[key] = ImageFont.truetype(fpath, size)
        else:
            fb = "/System/Library/Fonts/Supplemental/Arial.ttf"
            _font_cache[key] = ImageFont.truetype(fb, size) if os.path.exists(fb) else ImageFont.load_default()
    return _font_cache[key]


# ─── PALETTES ──────────────────────────────────────────────────

PALETTES = {
    "midnight": {
        "bg": (26, 26, 46), "surface": (15, 52, 96),
        "text": (240, 240, 245), "text_secondary": (195, 205, 225),
        "accent": (78, 154, 241), "highlight": (255, 200, 60),
        "danger": (235, 75, 75), "muted": (100, 110, 135),
        "gradient_top": (30, 30, 55), "gradient_bot": (12, 12, 28),
    },
    "slate": {
        "bg": (30, 42, 58), "surface": (40, 56, 76),
        "text": (238, 241, 245), "text_secondary": (180, 195, 220),
        "accent": (142, 172, 205), "highlight": (255, 210, 70),
        "danger": (220, 80, 70), "muted": (90, 105, 130),
        "gradient_top": (35, 50, 68), "gradient_bot": (18, 28, 42),
    },
    "coral": {
        "bg": (61, 12, 17), "surface": (80, 20, 28),
        "text": (255, 255, 255), "text_secondary": (255, 210, 210),
        "accent": (233, 69, 96), "highlight": (255, 220, 100),
        "danger": (255, 100, 100), "muted": (160, 100, 110),
        "gradient_top": (70, 18, 24), "gradient_bot": (40, 8, 12),
    },
    "noir": {
        "bg": (18, 18, 22), "surface": (30, 30, 38),
        "text": (245, 245, 250), "text_secondary": (190, 190, 210),
        "accent": (212, 175, 55), "highlight": (255, 215, 0),
        "danger": (200, 50, 50), "muted": (100, 100, 120),
        "gradient_top": (25, 25, 32), "gradient_bot": (8, 8, 14),
    },
    "clean": {
        "bg": (255, 255, 255), "surface": (245, 245, 250),
        "text": (20, 20, 30), "text_secondary": (80, 80, 100),
        "accent": (30, 100, 200), "highlight": (20, 60, 160),
        "danger": (200, 40, 40), "muted": (150, 150, 165),
        "gradient_top": (255, 255, 255), "gradient_bot": (240, 242, 248),
    },
    "warm": {
        "bg": (245, 236, 215), "surface": (235, 222, 195),
        "text": (50, 35, 20), "text_secondary": (100, 80, 60),
        "accent": (180, 100, 40), "highlight": (140, 70, 20),
        "danger": (180, 50, 30), "muted": (140, 120, 100),
        "gradient_top": (250, 242, 225), "gradient_bot": (235, 220, 195),
    },
}


# ─── BLUEPRINT SPEC ────────────────────────────────────────────
#
# A blueprint is a JSON document describing the PDF to generate:
#
# {
#   "page_size": [1080, 1350],      // width, height in px
#   "margin": [70, 90],             // [x_margin, y_margin_top/bot]
#   "theme": "midnight",            // palette name
#   "bg_image": "path/to/photo.jpg",  // optional global bg
#   "pages": [
#     {
#       "type": "cover",            // page type determines layout
#       "bg_image": "path.jpg",     // optional per-page bg
#       "blocks": [
#         {
#           "role": "title_heavy",
#           "text": "Main Title Here",
#           "color": "highlight",
#           "align": "center",
#           "size": "fill",         // "fill" = auto-scale, or number = fixed px
#         },
#         {
#           "role": "body_medium",
#           "text": "Subtitle text",
#           "color": "text_secondary",
#           "align": "center",
#           "size": 28,
#         }
#       ]
#     },
#     {
#       "type": "body",
#       "blocks": [
#         {"role": "title_bold", "text": "Section Title", "color": "accent", "align": "left", "size": "fill"},
#         {"role": "body_regular", "text": "Body paragraph text here...", "color": "text_secondary", "align": "left", "size": "fill"},
#         {"role": "body_semibold", "text": "Key data point", "color": "accent", "align": "left", "size": "fill", "prefix": "→"},
#         {"role": "muted", "text": "Source: Reuters", "color": "muted", "align": "left", "size": "fill", "prefix": "—"},
#       ]
#     }
#   ]
# }
#
# Block fields:
#   role:     font role (title_heavy, body_regular, etc.)
#   text:     content string
#   color:    palette key (text, accent, highlight, muted, danger, etc.)
#   align:    "center" or "left"
#   size:     "fill" (auto-scale to fill page) or integer (fixed px)
#   prefix:   optional prefix character (→, —, etc.) rendered in same style
#   spacing:  "tight" (1.2), "normal" (1.35), "loose" (1.5) — default "normal"


# ─── TEXT HELPERS ──────────────────────────────────────────────

def wrap_text(text, font, max_width, draw):
    words = text.split()
    lines, current = [], ""
    for word in words:
        test = f"{current} {word}".strip()
        if draw.textbbox((0, 0), test, font=font)[2] <= max_width:
            current = test
        else:
            if current: lines.append(current)
            current = word
    if current: lines.append(current)
    return lines or [""]


INTER_BLOCK_GAP = 0.55  # fraction of line height between distinct blocks


def measure_blocks(blocks, max_w, draw, font_scale=1.0, spacing_mult=1.35):
    """Measure total height of rendered blocks. Returns (items, total_h)."""
    BASE_SIZES = {
        "title_heavy": 58, "title_bold": 46, "title_semibold": 38, "title_medium": 34,
        "body_extrabold": 48, "body_bold": 38, "body_semibold": 34,
        "body_medium": 30, "body_regular": 28, "body_light": 24,
        "label": 16, "muted": 20, "mono": 22,
    }
    SPACING = {"tight": 1.2, "normal": 1.35, "loose": 1.5}

    rendered = []
    for block_idx, block in enumerate(blocks):
        sp = SPACING.get(block.get("spacing", "normal"), 1.35)
        role = block.get("role", "body_regular")
        text = block.get("text", "")
        fixed_size = block.get("size")

        if fixed_size and fixed_size != "fill":
            size = int(fixed_size)
        else:
            base = BASE_SIZES.get(role, 28)
            size = max(14, int(base * font_scale))

        font = get_font(role, size)
        prefix = block.get("prefix", "")
        full_text = f"{prefix} {text}".strip() if prefix else text

        wrapped = wrap_text(full_text, font, max_w, draw)
        for i, wl in enumerate(wrapped):
            bbox = draw.textbbox((0, 0), wl, font=font)
            h = bbox[3] - bbox[1]
            is_block_end = (i == len(wrapped) - 1) and (block_idx < len(blocks) - 1)
            rendered.append({"text": wl, "font": font, "block": block, "height": h, "spacing": sp, "is_block_end": is_block_end})

    total_h = 0
    for r in rendered:
        total_h += int(r["height"] * r["spacing"])
        if r.get("is_block_end"):
            total_h += int(r["height"] * INTER_BLOCK_GAP)
    return rendered, total_h


def find_fill_scale(blocks, max_w, max_h, draw):
    """Binary search for largest font_scale that fills ~85%."""
    lo, hi = 0.4, 2.8
    best = 1.0
    for _ in range(25):
        mid = (lo + hi) / 2
        _, total_h = measure_blocks(blocks, max_w, draw, font_scale=mid)
        fill = total_h / max_h if max_h > 0 else 0
        if fill < 0.80: lo = mid; best = mid
        elif fill > 0.92: hi = mid
        else: best = mid; break
    return best


def draw_blocks(draw, blocks, palette, page_w, page_h, margin_x, margin_top, margin_bot):
    """Render blocks onto a page. Handles fill-scale and alignment."""
    max_w = page_w - (margin_x * 2) - 40
    max_h = page_h - margin_top - margin_bot

    # Check if any block uses "fill" sizing
    needs_fill = any(b.get("size") == "fill" or not b.get("size") for b in blocks)
    scale = find_fill_scale(blocks, max_w, max_h, draw) if needs_fill else 1.0

    rendered, total_h = measure_blocks(blocks, max_w, draw, font_scale=scale)
    y = margin_top + (max_h - total_h) // 2

    for r in rendered:
        text, font = r["text"], r["font"]
        block = r["block"]
        color_key = block.get("color", "text")
        color = palette.get(color_key, palette["text"])
        align = block.get("align", "center")

        bbox = draw.textbbox((0, 0), text, font=font)
        w = bbox[2] - bbox[0]

        if align == "center":
            x = (page_w - w) // 2
        else:
            x = margin_x

        draw.text((x, y), text, fill=color, font=font)
        y += int(r["height"] * r["spacing"])
        if r.get("is_block_end"):
            y += int(r["height"] * INTER_BLOCK_GAP)


# ─── BACKGROUNDS ───────────────────────────────────────────────

def make_gradient(palette, w, h):
    img = Image.new("RGB", (w, h), palette["bg"])
    draw = ImageDraw.Draw(img)
    top = palette.get("gradient_top", palette["bg"])
    bot = palette.get("gradient_bot", palette["bg"])
    for y in range(h):
        r = y / h
        c = tuple(int(top[i] + (bot[i] - top[i]) * r) for i in range(3))
        draw.line([(0, y), (w, y)], fill=c)
    return img


def make_photo_bg(path, palette, w, h, darkness=0.45, blur=4, tint=0.45):
    photo = Image.open(path).convert("RGB").resize((w, h), Image.LANCZOS)
    photo = photo.filter(ImageFilter.GaussianBlur(radius=blur))
    photo = ImageEnhance.Brightness(photo).enhance(1.0 - darkness)
    overlay = Image.new("RGB", (w, h), palette["bg"])
    return Image.blend(photo, overlay, tint)


def make_solid(color, w, h):
    return Image.new("RGB", (w, h), color)


# ─── BLUEPRINT BUILDER ─────────────────────────────────────────

def build_from_blueprint(bp):
    """Build a PDF from a blueprint dict. Returns list of PIL Images."""
    page_w, page_h = bp.get("page_size", [1080, 1350])
    margins = bp.get("margin", [70, 90])
    mx, my = margins[0], margins[1]
    theme = bp.get("theme", "midnight")
    palette = PALETTES.get(theme, PALETTES["midnight"])
    global_bg = bp.get("bg_image")

    pages = []
    for page in bp.get("pages", []):
        # Background
        page_bg = page.get("bg_image") or global_bg
        page_type = page.get("type", "body")

        if page_bg and os.path.exists(str(page_bg)):
            img = make_photo_bg(str(page_bg), palette, page_w, page_h)
        elif page_type == "cover":
            img = make_gradient(palette, page_w, page_h)
        elif page_type == "accent":
            color = page.get("bg_color")
            if color:
                img = make_solid(tuple(color), page_w, page_h)
            else:
                img = make_solid(palette.get("danger", palette["bg"]), page_w, page_h)
        else:
            img = make_gradient(palette, page_w, page_h)

        draw = ImageDraw.Draw(img)
        blocks = page.get("blocks", [])

        # Optional label
        if page.get("label"):
            draw.text((mx, my - 10), page["label"],
                      fill=palette["muted"], font=get_font("label", 14))

        draw_blocks(draw, blocks, palette, page_w, page_h, mx, my, my)
        pages.append(img)

    return pages


# ─── PDF ASSEMBLY ──────────────────────────────────────────────

def save_pdf(pages, output_path, page_size):
    pdf = FPDF(unit="pt", format=page_size)
    tmps = []
    for i, img in enumerate(pages):
        tmp = f"/tmp/_pdfmaker_{i}.png"
        img.save(tmp, "PNG", dpi=(DPI, DPI))
        tmps.append(tmp)
        pdf.add_page()
        pdf.image(tmp, x=0, y=0, w=page_size[0], h=page_size[1])
    pdf.output(output_path)
    for f in tmps: os.unlink(f)
    mb = os.path.getsize(output_path) / (1024*1024)
    print(f"✅ {output_path} ({len(pages)} pages, {mb:.1f} MB)")


# ─── BLUEPRINT BUILDERS ────────────────────────────────────────
# Helper functions to create blueprints for common document types.

def blueprint_report(title, subtitle, sections, theme="midnight", page_size=None):
    """Create a multi-page report blueprint.
    sections: [{"title": "...", "items": [{"role": "...", "text": "...", ...}]}]
    """
    ps = page_size or [1080, 1350]
    mx, my = 70, 90

    pages = []
    # Cover page
    cover_blocks = [
        {"role": "title_heavy", "text": title, "color": "text", "align": "center", "size": "fill"},
        {"role": "body_medium", "text": subtitle, "color": "text_secondary", "align": "center", "size": "fill"},
    ]
    pages.append({"type": "cover", "blocks": cover_blocks})

    # Section pages
    for section in sections:
        blocks = []
        if section.get("title"):
            blocks.append({"role": "title_bold", "text": section["title"], "color": "accent", "align": "left", "size": "fill"})
        for item in section.get("items", []):
            blocks.append({**item, "size": item.get("size", "fill")})
        if blocks:
            pages.append({"type": "body", "blocks": blocks})

    return {"page_size": ps, "margin": [mx, my], "theme": theme, "pages": pages}


def blueprint_carousel(slides_data, theme="midnight"):
    """Create a carousel blueprint from slide data.
    slides_data: [{"type": "HOOK", "lines": ["text", ...]}, ...]
    """
    ps = [1080, 1350]
    mx, my = 70, 90

    pages = []
    for slide in slides_data:
        stype = slide.get("type", "CORE")
        lines = slide.get("lines", [])
        blocks = []

        for line in lines:
            line = line.strip()
            if not line: continue

            # Classify
            if line.startswith("**") and line.endswith("**"):
                blocks.append({"role": "title_heavy", "text": line.strip("*"), "color": "highlight", "align": "center" if stype in ("HOOK", "CUT", "QUESTION") else "left"})
            elif line.startswith("**"):
                blocks.append({"role": "title_bold", "text": line.replace("**", ""), "color": "text", "align": "center" if stype in ("HOOK", "CUT", "QUESTION") else "left"})
            elif line.startswith("→"):
                arrow_align = "center" if stype in ("HOOK", "CUT", "QUESTION") else "left"
                blocks.append({"role": "body_semibold", "text": line.lstrip("→ "), "color": "accent", "align": arrow_align, "prefix": "→"})
            elif line.startswith("—"):
                dash_align = "center" if stype in ("HOOK", "CUT", "QUESTION") else "left"
                blocks.append({"role": "muted", "text": line.lstrip("— "), "color": "muted", "align": dash_align, "prefix": "—"})
            else:
                align = "center" if stype in ("HOOK", "CUT", "QUESTION") else "left"
                blocks.append({"role": "body_regular", "text": line, "color": "text_secondary", "align": align})

        # Special page types
        page = {"type": "body", "blocks": blocks}
        if stype == "HOOK":
            page["type"] = "cover"
        elif stype == "CUT":
            # Check if thesis line → red bg
            full = " ".join(lines).lower()
            if any(kw in full for kw in ["chose the employee", "chose itself"]):
                page["type"] = "accent"
                for b in page["blocks"]:
                    if b.get("color") in ("highlight", "text"):
                        b["color"] = "text"
                    else:
                        b["color"] = "text_secondary"
        elif stype == "COUNTER":
            page["label"] = "THE OTHER SIDE"

        pages.append(page)

    return {"page_size": ps, "margin": [mx, my], "theme": theme, "pages": pages}


# ─── CLI ───────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="PDF Maker — generate PDFs from blueprints or markdown")
    sub = parser.add_subparsers(dest="command")

    # build from JSON blueprint
    p_build = sub.add_parser("build", help="Build PDF from JSON blueprint")
    p_build.add_argument("blueprint", help="JSON blueprint file")
    p_build.add_argument("--output", "-o", required=True, help="Output PDF path")
    p_build.add_argument("--bg-image", help="Global background image")

    # from markdown (simple)
    p_md = sub.add_parser("from-md", help="Build PDF from markdown file")
    p_md.add_argument("input", help="Markdown file")
    p_md.add_argument("--output", "-o", help="Output PDF path")
    p_md.add_argument("--theme", "-t", choices=list(PALETTES.keys()), default="midnight")
    p_md.add_argument("--type", choices=["report", "article", "slides"], default="report")
    p_md.add_argument("--bg-image", help="Background image")
    p_md.add_argument("--page-size", choices=["portrait", "landscape", "square"], default="portrait")

    args = parser.parse_args()

    if args.command == "build":
        with open(args.blueprint) as f:
            bp = json.load(f)
        if args.bg_image:
            bp["bg_image"] = args.bg_image
        pages = build_from_blueprint(bp)
        save_pdf(pages, args.output, bp.get("page_size", [1080, 1350]))

    elif args.command == "from-md":
        sizes = {"portrait": [1080, 1350], "landscape": [1350, 1080], "square": [1080, 1080]}
        ps = sizes[args.page_size]
        out = args.output or os.path.join(
            os.path.expanduser("~/.nexus/vault"),
            Path(args.input).stem + ".pdf"
        )

        with open(args.input) as f:
            content = f.read()

        # Extract title and sections from markdown
        lines = content.split("\n")
        title = ""
        subtitle = ""
        sections = []
        current_section = None

        for line in lines:
            if line.startswith("# ") and not title:
                title = line.lstrip("# ").strip()
            elif line.startswith("## ") and title:
                if current_section:
                    sections.append(current_section)
                current_section = {"title": line.lstrip("## ").strip(), "items": []}
            elif line.startswith("> ") and not subtitle:
                subtitle = line.lstrip("> ").strip()
            elif current_section and line.strip():
                text = line.strip().lstrip("- ").strip()
                if text:
                    is_bold = text.startswith("**")
                    current_section["items"].append({
                        "role": "body_bold" if is_bold else "body_regular",
                        "text": text.replace("**", ""),
                        "color": "text" if is_bold else "text_secondary",
                        "align": "left"
                    })

        if current_section:
            sections.append(current_section)

        bp = blueprint_report(title, subtitle, sections, theme=args.theme, page_size=ps)
        if args.bg_image:
            bp["bg_image"] = args.bg_image
        pages = build_from_blueprint(bp)
        save_pdf(pages, out, ps)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
