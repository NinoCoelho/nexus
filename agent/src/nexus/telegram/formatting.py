"""Assistant markdown → Telegram HTML conversion.

Telegram accepts a tiny HTML subset (``<b> <i> <u> <s> <code> <pre> <a>
<blockquote>``) and preserves literal newlines. Python-Markdown collapses
newlines and mangles unterminated fences — which *will* happen while we
stream-edit partially generated text — so this is a streaming-tolerant
line-based converter instead:

- unclosed ``` fences are closed implicitly (safe for partial output),
- newlines are preserved verbatim,
- inline emphasis/code/links are regex-based with code spans stashed so
  they're never double-processed.

Everything else falls through as escaped text; if Telegram still refuses
to parse the result, callers retry with the raw text and no parse_mode.
"""

from __future__ import annotations

import re

# Telegram counts message length in UTF-16 code units (emoji = 2).
def tlen(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def _escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_STASH = "\x00{}\x00"
_STASH_RE = re.compile("\x00(\\d+)\x00")
_CODE_RE = re.compile(r"`([^`\n]+)`")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_ITAL_RE = re.compile(r"(?<![\w*])\*([^*\n]+)\*(?!\w)|(?<![\w_])_([^_\n]+)_(?!\w)")
_STRIKE_RE = re.compile(r"~~(.+?)~~")
_MD_LINK_RE = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)")
_URL_RE = re.compile(r"(?<![\w\"'>=])(https?://[^\s<\x00]+)")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET_RE = re.compile(r"^[-*+]\s+(.*)$")
_NUM_RE = re.compile(r"^(\d+)[.)]\s+(.*)$")


def _inline(escaped: str) -> str:
    """Inline markdown → HTML on an already-escaped line."""
    stash: list[str] = []

    def _keep(html: str) -> str:
        stash.append(html)
        return _STASH.format(len(stash) - 1)

    # 1. code spans — stashed first so nothing else touches them
    def _code(m: re.Match) -> str:
        return _keep(f"<code>{m.group(1)}</code>")

    s = _CODE_RE.sub(_code, escaped)

    # 2. markdown links — validated http(s)/mailto only, then stashed so the
    #    bare-URL autolinker can't nest a second <a> inside them.
    def _link(m: re.Match) -> str:
        label, url = m.group(1), m.group(2)
        if not re.match(r"^(https?://|mailto:)", url, re.I):
            return m.group(0)
        href = url.replace('"', "%22")
        return _keep(f'<a href="{href}">{label}</a>')

    s = _MD_LINK_RE.sub(_link, s)

    # 3. emphasis
    s = _BOLD_RE.sub(lambda m: f"<b>{m.group(1) or m.group(2)}</b>", s)
    s = _ITAL_RE.sub(lambda m: f"<i>{m.group(1) or m.group(2)}</i>", s)
    s = _STRIKE_RE.sub(r"<s>\1</s>", s)

    # 4. bare URLs → autolink
    s = _URL_RE.sub(lambda m: _keep(f'<a href="{m.group(1)}">{m.group(1)}</a>'), s)

    # 5. restore stashed html
    return _STASH_RE.sub(lambda m: stash[int(m.group(1))], s)


def md_to_telegram_html(md: str) -> str:
    lines = md.split("\n")
    out: list[str] = []
    in_pre = False
    in_quote = False

    def _close_quote() -> None:
        nonlocal in_quote
        if in_quote:
            out.append("</blockquote>")
            in_quote = False

    for line in lines:
        stripped = line.strip()

        if not in_pre and stripped.startswith("```"):
            _close_quote()
            out.append("<pre>")
            in_pre = True
            continue

        if in_pre:
            if stripped.endswith("```") and len(stripped) >= 3:
                out.append("</pre>")
                in_pre = False
            else:
                out.append(_escape(line))
            continue

        if not stripped:
            _close_quote()
            out.append("")
            continue

        m = _HEADING_RE.match(stripped)
        if m:
            _close_quote()
            out.append(f"<b>{_inline(_escape(m.group(2)))}</b>")
            continue

        if stripped.startswith("> ") or stripped == ">":
            if not in_quote:
                out.append("<blockquote>")
                in_quote = True
            content = stripped[2:] if stripped.startswith("> ") else ""
            out.append(_inline(_escape(content)) if content else "")
            continue

        if re.fullmatch(r"[-*_]{3,}", stripped):  # horizontal rule
            _close_quote()
            out.append("─" * 20)
            continue

        m = _BULLET_RE.match(stripped)
        if m:
            _close_quote()
            out.append(f"• {_inline(_escape(m.group(1)))}")
            continue

        m = _NUM_RE.match(stripped)
        if m:
            _close_quote()
            out.append(f"{m.group(1)}. {_inline(_escape(m.group(2)))}")
            continue

        _close_quote()
        out.append(_inline(_escape(line)))

    if in_pre:
        out.append("</pre>")
    _close_quote()
    return "\n".join(out)


def _pre_balance(chunk: str) -> tuple[str, str]:
    """Repair a chunk that was cut inside a <pre> block.

    Returns ``(chunk_with_closing_tag, reopening_tag_for_next_chunk)``.
    """
    if chunk.count("<pre>") > chunk.count("</pre>"):
        return chunk + "</pre>", "<pre>"
    return chunk, ""


def split_for_telegram(html: str, limit: int = 4000) -> list[str]:
    """Split long HTML into ≤``limit`` Telegram messages.

    Splits on paragraph boundaries, then lines, then hard-wraps. Chunks cut
    inside a ``<pre>`` get their tags balanced so each chunk parses.
    """
    if tlen(html) <= limit:
        return [html]

    chunks: list[str] = []
    pending_reopen = ""

    def _emit(piece: str) -> None:
        nonlocal pending_reopen
        if pending_reopen:
            piece = pending_reopen + piece
            pending_reopen = ""
        if tlen(piece) <= limit:
            balanced, reopen = _pre_balance(piece)
            chunks.append(balanced)
            pending_reopen = reopen
            return
        # hard-wrap this piece, balancing pre tags across cuts (room is
        # reserved for the reopening prefix AND the closing tag that
        # _pre_balance may append to this chunk)
        while tlen(pending_reopen + piece) > limit:
            prefix = pending_reopen
            budget = max(1, limit - tlen(prefix) - len("</pre>"))
            cut = piece[:budget]
            # try to end on a newline for readability
            nl = cut.rfind("\n")
            if nl > budget // 2:
                cut = cut[: nl + 1]
            piece = piece[len(cut):]
            full = prefix + cut
            pending_reopen = ""
            balanced, reopen = _pre_balance(full)
            chunks.append(balanced)
            pending_reopen = reopen
        if piece or pending_reopen:
            full = pending_reopen + piece
            pending_reopen = ""
            balanced, reopen = _pre_balance(full)
            chunks.append(balanced)
            pending_reopen = reopen

    current = ""
    for para in html.split("\n\n"):
        candidate = f"{current}\n\n{para}" if current else para
        if tlen(candidate) <= limit:
            current = candidate
            continue
        if current:
            _emit(current)
            current = ""
        if tlen(para) > limit:
            for line in para.split("\n"):
                candidate = f"{current}\n{line}" if current else line
                if tlen(candidate) <= limit:
                    current = candidate
                else:
                    if current:
                        _emit(current)
                    _emit(line)
                    current = ""
        else:
            current = para
    if current:
        _emit(current)

    return [c for c in chunks if c.strip()]
