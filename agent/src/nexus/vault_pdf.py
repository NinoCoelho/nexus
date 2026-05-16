"""Convert vault markdown files to styled PDF using Inter + Montserrat."""

from __future__ import annotations

import html
import io
import re
from pathlib import Path
from xml.etree.ElementTree import fromstring, ParseError

import markdown
from fpdf import FPDF

FONTS_DIR = Path.home() / ".nexus" / "fonts"

_FONT_FAMILIES = {
    "Inter": [
        ("Thin", "Inter-Thin.ttf"),
        ("Light", "Inter-Light.ttf"),
        ("Regular", "Inter-Regular.ttf"),
        ("Medium", "Inter-Medium.ttf"),
        ("SemiBold", "Inter-SemiBold.ttf"),
        ("Bold", "Inter-Bold.ttf"),
        ("ExtraBold", "Inter-ExtraBold.ttf"),
        ("Black", "Inter-Black.ttf"),
    ],
    "Montserrat": [
        ("Regular", "Montserrat-Regular.ttf"),
        ("Medium", "Montserrat-Medium.ttf"),
        ("SemiBold", "Montserrat-SemiBold.ttf"),
        ("Bold", "Montserrat-Bold.ttf"),
        ("ExtraBold", "Montserrat-ExtraBold.ttf"),
    ],
}


_BOLD_WEIGHTS = {"Bold", "ExtraBold", "Black", "BoldItalic", "ExtraBoldItalic", "BlackItalic"}


def _register_fonts(pdf: FPDF) -> None:
    for family, styles in _FONT_FAMILIES.items():
        for style_name, filename in styles:
            path = FONTS_DIR / filename
            if not path.exists():
                continue
            is_bold = style_name in _BOLD_WEIGHTS
            is_italic = "Italic" in style_name and style_name != "Italic"
            if is_bold and is_italic:
                style = "BI"
            elif is_bold:
                style = "B"
            elif is_italic:
                style = "I"
            else:
                style = ""
            pdf.add_font(family, style, str(path))


def _has_fonts() -> bool:
    return (FONTS_DIR / "Inter-Regular.ttf").is_file() and (
        FONTS_DIR / "Montserrat-Regular.ttf"
    ).is_file()


_H_FONT = "Inter"
_B_FONT = "Montserrat"

_BODY_SIZE = 10
_H1_SIZE = 20
_H2_SIZE = 15
_H3_SIZE = 12
_H4_SIZE = 11
_CODE_SIZE = 8.5

_MARGIN = 18


def _strip_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end == -1:
        return text
    return text[end + 4 :].lstrip("\n")


def _title_from_path(rel_path: str) -> str:
    stem = Path(rel_path).stem
    return re.sub(r"[-_]+", " ", stem).strip().title()


def _unescape(text: str) -> str:
    return html.unescape(text)


class _PdfBuilder:
    def __init__(self, pdf: FPDF):
        self.pdf = pdf
        self._in_pre = False
        self._in_blockquote = False
        self._list_depth = 0

    def feed(self, html_str: str) -> None:
        try:
            root = fromstring(f"<root>{html_str}</root>")
        except ParseError:
            root = fromstring(f"<root>{html.escape(html_str)}</root>")
        self._render_children(root)

    def _render_children(self, el) -> None:
        for child in el:
            tag = child.tag if isinstance(child.tag, str) else ""
            if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
                self._heading(child)
            elif tag == "p":
                self._paragraph(child)
            elif tag in ("ul", "ol"):
                self._list(child, ordered=(tag == "ol"))
            elif tag == "pre":
                self._pre_block(child)
            elif tag == "blockquote":
                self._blockquote(child)
            elif tag == "hr":
                self._hr()
            elif tag == "table":
                self._table(child)
            elif tag in ("div", "section", "article", "main", "span"):
                self._render_children(child)
            else:
                self._inline(child)

    def _check_space(self, h: float = 10) -> None:
        if self.pdf.get_y() + h > self.pdf.h - self.pdf.b_margin:
            self.pdf.add_page()

    def _heading(self, el) -> None:
        tag = el.tag
        sizes = {"h1": _H1_SIZE, "h2": _H2_SIZE, "h3": _H3_SIZE, "h4": _H4_SIZE}
        size = sizes.get(tag, _H4_SIZE)
        text = _unescape(el.text_content()) if hasattr(el, "text_content") else _unescape("".join(el.itertext()))
        text = text.strip()
        if not text:
            return
        self._check_space(size + 8)
        self.pdf.ln(4)
        self.pdf.set_font(_H_FONT, "B", size)
        self.pdf.multi_cell(0, size * 0.5, text, new_x="LMARGIN", new_y="NEXT")
        self.pdf.ln(2)

    def _paragraph(self, el) -> None:
        text = self._collect_text(el).strip()
        if not text:
            return
        self._check_space()
        self.pdf.set_font(_B_FONT, "", _BODY_SIZE)
        self.pdf.multi_cell(0, _BODY_SIZE * 1.0, text, new_x="LMARGIN", new_y="NEXT")
        self.pdf.ln(2)

    def _collect_text(self, el) -> str:
        parts = []
        if el.text:
            parts.append(_unescape(el.text))
        for child in el:
            tag = child.tag if isinstance(child.tag, str) else ""
            if tag in ("strong", "b"):
                parts.append(_unescape(child.text or ""))
                if child.tail:
                    parts.append(_unescape(child.tail))
            elif tag in ("em", "i"):
                parts.append(_unescape(child.text or ""))
                if child.tail:
                    parts.append(_unescape(child.tail))
            elif tag in ("code",):
                parts.append(_unescape(child.text or ""))
                if child.tail:
                    parts.append(_unescape(child.tail))
            elif tag == "br":
                parts.append("\n")
                if child.tail:
                    parts.append(_unescape(child.tail))
            elif tag == "a":
                parts.append(_unescape(child.text or ""))
                if child.tail:
                    parts.append(_unescape(child.tail))
            else:
                parts.append(_unescape("".join(child.itertext())))
                if child.tail:
                    parts.append(_unescape(child.tail))
        return "".join(parts)

    def _list(self, el, ordered: bool = False) -> None:
        self._list_depth += 1
        indent = (self._list_depth - 1) * 8
        counter = 0
        for child in el:
            if child.tag != "li":
                continue
            counter += 1
            text = self._collect_text(child).strip()
            if not text:
                continue
            self._check_space()
            bullet = f"{counter}." if ordered else "\u2022"
            self.pdf.set_font(_B_FONT, "", _BODY_SIZE)
            x = self.pdf.l_margin + indent
            self.pdf.set_x(x)
            self.pdf.cell(6, _BODY_SIZE * 1.0, bullet, new_x="END")
            self.pdf.multi_cell(
                self.pdf.w - x - 6 - self.pdf.r_margin,
                _BODY_SIZE * 1.0,
                text,
                new_x="LMARGIN",
                new_y="NEXT",
            )
            self.pdf.ln(1)
        self._list_depth -= 1
        if self._list_depth == 0:
            self.pdf.ln(2)

    def _pre_block(self, el) -> None:
        text = (el.text or "").rstrip("\n")
        if not text:
            return
        lines = text.split("\n")
        line_h = _CODE_SIZE * 1.5
        block_h = len(lines) * line_h + 8
        self._check_space(block_h)
        self.pdf.ln(2)
        x = self.pdf.l_margin
        y = self.pdf.get_y()
        w = self.pdf.w - self.pdf.l_margin - self.pdf.r_margin
        self.pdf.set_fill_color(245, 245, 245)
        self.pdf.rect(x, y, w, block_h, style="F")
        self.pdf.set_xy(x + 4, y + 4)
        self.pdf.set_font("Courier", "", _CODE_SIZE)
        for line in lines:
            self.pdf.set_x(x + 4)
            self.pdf.cell(w - 8, line_h, line, new_x="LMARGIN", new_y="NEXT")
        self.pdf.ln(4)

    def _blockquote(self, el) -> None:
        text = self._collect_text(el).strip()
        if not text:
            return
        self._check_space()
        x = self.pdf.l_margin
        y = self.pdf.get_y()
        self.pdf.set_draw_color(180, 180, 180)
        self.pdf.set_line_width(0.8)
        self.pdf.line(x, y, x, y + 20)
        self.pdf.set_x(x + 8)
        self.pdf.set_font(_B_FONT, "", _BODY_SIZE)
        self.pdf.set_text_color(100, 100, 100)
        self.pdf.multi_cell(
            self.pdf.w - x - 8 - self.pdf.r_margin,
            _BODY_SIZE * 1.0,
            text,
            new_x="LMARGIN",
            new_y="NEXT",
        )
        self.pdf.set_text_color(0, 0, 0)
        self.pdf.ln(3)

    def _hr(self) -> None:
        self._check_space()
        self.pdf.ln(4)
        y = self.pdf.get_y()
        self.pdf.set_draw_color(200, 200, 200)
        self.pdf.set_line_width(0.3)
        self.pdf.line(self.pdf.l_margin, y, self.pdf.w - self.pdf.r_margin, y)
        self.pdf.ln(4)

    def _table(self, el) -> None:
        rows = []
        for tr in el.iter("tr"):
            cells = []
            for td in tr:
                if td.tag in ("td", "th"):
                    cells.append(_unescape("".join(td.itertext())).strip())
            if cells:
                rows.append((cells, tr.tag == "th" or tr.get("parent") == "th"))
        if not rows:
            return
        n_cols = max(len(r[0]) for r in rows)
        col_w = (self.pdf.w - self.pdf.l_margin - self.pdf.r_margin) / n_cols
        for cells, is_header in rows:
            self._check_space()
            if is_header:
                self.pdf.set_font(_B_FONT, "B", _BODY_SIZE)
                self.pdf.set_fill_color(235, 235, 235)
            else:
                self.pdf.set_font(_B_FONT, "", _BODY_SIZE)
                self.pdf.set_fill_color(255, 255, 255)
            for i, cell_text in enumerate(cells):
                self.pdf.cell(col_w, 7, cell_text, border=1, fill=is_header)
            self.pdf.ln()
        self.pdf.ln(3)

    def _inline(self, el) -> None:
        text = self._collect_text(el).strip()
        if text:
            self._check_space()
            self.pdf.set_font(_B_FONT, "", _BODY_SIZE)
            self.pdf.multi_cell(0, _BODY_SIZE * 1.0, text, new_x="LMARGIN", new_y="NEXT")
            self.pdf.ln(2)


def markdown_to_pdf(body: str, title: str = "") -> bytes:
    body = _strip_frontmatter(body)
    extensions = ["tables", "fenced_code", "toc", "sane_lists"]
    html_body = markdown.markdown(body, extensions=extensions)

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=_MARGIN)
    pdf.set_left_margin(_MARGIN)
    pdf.set_right_margin(_MARGIN)

    if _has_fonts():
        _register_fonts(pdf)

    pdf.add_page()

    if title:
        pdf.set_font(_H_FONT, "B", _H1_SIZE)
        pdf.multi_cell(0, _H1_SIZE * 0.55, title, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)
        y = pdf.get_y()
        pdf.set_draw_color(200, 200, 200)
        pdf.set_line_width(0.4)
        pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
        pdf.ln(6)

    builder = _PdfBuilder(pdf)
    builder.feed(html_body)

    buf = io.BytesIO()
    pdf.output(buf)
    return buf.getvalue()


def vault_file_to_pdf(rel_path: str) -> bytes:
    from .vault import resolve_path, read_file

    full = resolve_path(rel_path)
    if not full.is_file():
        raise FileNotFoundError(rel_path)

    result = read_file(rel_path)
    content = result.get("content", "")
    if result.get("binary"):
        raise ValueError(f"Cannot export binary file as PDF: {rel_path}")

    title = _title_from_path(rel_path)
    return markdown_to_pdf(content, title=title)
