"""Pre-format arbitrary text so it sounds like a person reading it aloud.

Run **before** any TTS call (see ``dispatch.synthesize``). Pure functions —
no I/O, no LLM calls. Detected language drives number / date / time
expansion; everything else is language-agnostic.

Currently first-class: ``en``, ``pt``. Other languages get the language-
agnostic transformations only (emojis, URLs, tables, diagrams, whitespace).

Order of operations matters:
  1. Detect language (cheap)
  2. Replace fenced code / mermaid blocks with a phrase BEFORE we strip
     any pipes / backticks
  3. Replace markdown tables with a phrase
  4. Strip emojis
  5. Replace URLs with "link to <domain>"
  6. Expand dates → words
  7. Expand times → words
  8. Expand numbers → words
  9. Collapse whitespace
"""

from __future__ import annotations

import logging
import re
from typing import Iterable
from urllib.parse import urlparse

log = logging.getLogger(__name__)


# ── Language-agnostic ──────────────────────────────────────────────────────


# Cover the common emoji blocks. Not exhaustive (Unicode keeps adding
# more) but catches everything users actually paste.
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"  # symbols & pictographs, supplementals
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F900-\U0001F9FF"  # supplemental symbols and pictographs
    "\U00002600-\U000027BF"  # miscellaneous symbols + dingbats
    "\U0001F1E6-\U0001F1FF"  # regional indicators (flags)
    "\U0000FE00-\U0000FE0F"  # variation selectors
    "\U0000200D"             # zero-width joiner (used in compound emoji)
    "]+",
    flags=re.UNICODE,
)


_URL_RE = re.compile(r"https?://[^\s\)\]<>]+", flags=re.IGNORECASE)


# Long hex strings: md5 (32), sha1 (40), sha256 (64), most session IDs,
# request IDs, commit hashes. 16+ hex chars is the floor — real words
# in any language never reach that with the alphabet restricted to
# [0-9a-f]. We catch case-insensitively.
_HEX_HASH_RE = re.compile(r"\b[0-9a-fA-F]{16,}\b")

# UUIDs in the canonical 8-4-4-4-12 form. Match BEFORE the bare-hex
# regex so the dashes are preserved during detection (the bare regex
# would otherwise miss UUIDs because they're under 16 contiguous chars).
_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)


# Fenced code blocks. We catch the LANGUAGE so mermaid/dot/sql/etc. can
# get a more specific announcement than just "a code block follows".
# Handles both ``` and ~~~ delimiters, with or without trailing newline.
_FENCED_RE = re.compile(
    r"(?P<open>```|~~~)(?P<lang>\w*)[^\n]*\n.*?\n?(?P=open)",
    flags=re.DOTALL,
)


# Markdown tables — header line + separator line + at least one row.
# Captured greedily so consecutive table rows roll into the same match.
_TABLE_RE = re.compile(
    r"(?:^\|[^\n]+\|\n)"      # header row
    r"\|[\s:|-]+\|\n"          # separator row (---|---)
    r"(?:\|[^\n]+\|\n?)+",     # ≥1 body row
    flags=re.MULTILINE,
)


def _strip_emojis(text: str) -> str:
    return _EMOJI_RE.sub("", text)


def _replace_urls(text: str) -> str:
    def _r(m: re.Match[str]) -> str:
        try:
            host = urlparse(m.group(0)).hostname or ""
        except Exception:  # noqa: BLE001
            host = ""
        return f"link to {host}" if host else "link"
    return _URL_RE.sub(_r, text)


def _replace_hashes(text: str, lang: str) -> str:
    """Replace UUIDs and long hex hashes with a short spoken placeholder.

    Reading a 32-char hex hash aloud takes ~30 seconds and conveys nothing.
    Listeners just need to know "an identifier was here" so the rest of
    the sentence makes sense; they can read the actual hash on screen.
    """
    placeholder = "(um identificador)" if lang == "pt" else "(an identifier)"
    # UUIDs first — they include dashes the bare-hex regex wouldn't span.
    text = _UUID_RE.sub(placeholder, text)
    text = _HEX_HASH_RE.sub(placeholder, text)
    return text


def _replace_fenced(text: str, lang: str) -> str:
    """Mermaid / code blocks → a brief spoken description in the user's lang."""
    is_pt = lang == "pt"
    def _r(m: re.Match[str]) -> str:
        block_lang = (m.group("lang") or "").lower()
        if block_lang == "mermaid":
            return "(diagrama a seguir)" if is_pt else "(a diagram follows)"
        if block_lang in ("", "text"):
            return "(trecho de código a seguir)" if is_pt else "(a code block follows)"
        return (
            f"(trecho de código em {block_lang} a seguir)" if is_pt
            else f"(a {block_lang} code block follows)"
        )
    return _FENCED_RE.sub(_r, text)


def _replace_tables(text: str, lang: str) -> str:
    """Markdown tables → 'table follows showing {first-row}, with N rows'."""
    is_pt = lang == "pt"
    def _r(m: re.Match[str]) -> str:
        block = m.group(0)
        rows = [r for r in block.splitlines() if r.strip().startswith("|")]
        if len(rows) < 2:
            return block  # not really a table
        header_cells = [c.strip() for c in rows[0].strip("|").split("|") if c.strip()]
        body_rows = max(0, len(rows) - 2)  # minus header + separator
        cols = ", ".join(header_cells) or ("colunas" if is_pt else "columns")
        if is_pt:
            return f"(tabela a seguir com colunas {cols}, com {body_rows} linhas)"
        return f"(a table follows showing {cols}, with {body_rows} rows)"
    return _TABLE_RE.sub(_r, text)


def _strip_markdown(text: str) -> str:
    """Remove markdown formatting so TTS reads plain text, not symbols.

    Runs AFTER fenced code blocks and tables (which are replaced with
    spoken placeholders by earlier passes) but BEFORE everything else,
    so asterisks / hashes / pipes don't leak through to the synthesizer.
    """

    # YAML frontmatter (--- ... ---) at the top of vault files.
    text = re.compile(r"^---\r?\n[\s\S]*?\r?\n---\r?\n?").sub("", text)

    # HTML comments (used by Nexus for nx:* markers).
    text = re.compile(r"<!--[\s\S]*?-->").sub("", text)

    # Math blocks ($$ ... $$) → spoken placeholder.
    text = re.compile(r"\$\$[\s\S]*?\$\$").sub(
        "(an equation)", text,
    )

    # Indented code blocks — 2+ consecutive lines starting with 4+ spaces
    # or a tab. Replace with a short placeholder.
    text = re.compile(
        r"(?:^(?: {4}|\t)[^\n]*\n?){2,}", flags=re.MULTILINE,
    ).sub("(a code block follows)", text)

    # Footnote references [^1] → remove.
    text = re.compile(r"\[\^[^\]]+\]").sub("", text)
    # Footnote definitions (at start of line) → remove whole line.
    text = re.compile(r"^\[\^[^\]]+\]:\s*[^\n]*\n?", flags=re.MULTILINE).sub(
        "", text,
    )

    # Task list markers: - [x] / - [ ] → "done: " or "".
    def _task_list(m: re.Match[str]) -> str:
        return "done: " if re.search(r"\[[xX]\]", m.group(0)) else ""
    text = re.compile(
        r"^\s*[-*+]\s+\[[ xX]\]\s*", flags=re.MULTILINE,
    ).sub(_task_list, text)

    # Images: ![alt](url) → alt
    text = re.compile(r"!\[([^\]]*)\]\([^)]*\)").sub(r"\1", text)

    # Links: [text](url) → text
    text = re.compile(r"\[([^\]]+)\]\([^)]*\)").sub(r"\1", text)

    # Reference-style links: [text][ref] → text
    text = re.compile(r"\[([^\]]+)\]\[[^\]]*\]").sub(r"\1", text)

    # Inline code → just the contents.
    text = re.compile(r"`([^`]+)`").sub(r"\1", text)

    # Escape sequences (\* \# etc.) → just the char.
    text = re.compile(r"""\\([\\`*_{}[\]()#+\-.!~|>])""").sub(r"\1", text)

    # Headings (# ## ### …) — drop the leading hashes.
    text = re.compile(r"^#{1,6}\s+", flags=re.MULTILINE).sub("", text)

    # Blockquotes
    text = re.compile(r"^>\s?", flags=re.MULTILINE).sub("", text)

    # Bold / italic / strikethrough — keep inner text only.
    text = re.compile(r"\*\*([^*]+)\*\*").sub(r"\1", text)
    text = re.compile(r"__([^_]+)__").sub(r"\1", text)
    text = re.compile(r"\*([^*\n]+)\*").sub(r"\1", text)
    text = re.compile(r"_([^_\n]+)_").sub(r"\1", text)
    text = re.compile(r"~~([^~]+)~~").sub(r"\1", text)

    # Horizontal rules (---, ***, ___).
    text = re.compile(r"^[-*_]{3,}\s*$", flags=re.MULTILINE).sub("", text)

    # Unordered list markers: -, *, +
    text = re.compile(r"^\s*[-*+]\s+", flags=re.MULTILINE).sub("", text)

    # Ordered list markers: 1. 2. etc.
    text = re.compile(r"^\s*\d+\.\s+", flags=re.MULTILINE).sub("", text)

    # Any stray pipes left over from non-table contexts.
    text = text.replace("|", " ")

    return text


def _collapse_whitespace(text: str) -> str:
    # Multiple newlines → single space; runs of spaces → single space.
    text = re.sub(r"\n{2,}", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


# ── Date + time + number expansion (en + pt) ───────────────────────────────


# Matches "12/07" or "12/07/2026" or "12-07" (dot separator avoided so
# decimals like 3.14 don't get parsed as dates).
_DATE_RE = re.compile(
    r"\b(?P<d>\d{1,2})[/-](?P<m>\d{1,2})(?:[/-](?P<y>\d{4}|\d{2}))?\b"
)

# Matches "14:30" or "14h30" — common BR + EN time formats.
_TIME_RE = re.compile(r"\b(?P<h>\d{1,2})[:h](?P<min>\d{2})\b")

# Numbers (integers + decimals + thousand separators). Skip things that look
# like dates / times — they get processed first.
# Examples: "1250", "1.250", "1,250", "3.14", "3,14"
_NUMBER_RE = re.compile(
    r"(?<![\d/.\-:hH])"             # not part of a date/time match
    r"(?P<n>\d{1,3}(?:[.,]\d{3})+|\d+)"  # 1.250 or plain digits
    r"(?:(?P<dec_sep>[.,])(?P<frac>\d+))?"  # optional fractional part
    r"(?![\d/])"                    # not followed by another digit/slash
)


_PT_MONTHS = (
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
)
_EN_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
_EN_ORDINALS = (
    "first", "second", "third", "fourth", "fifth", "sixth", "seventh",
    "eighth", "ninth", "tenth", "eleventh", "twelfth", "thirteenth",
    "fourteenth", "fifteenth", "sixteenth", "seventeenth", "eighteenth",
    "nineteenth", "twentieth", "twenty-first", "twenty-second",
    "twenty-third", "twenty-fourth", "twenty-fifth", "twenty-sixth",
    "twenty-seventh", "twenty-eighth", "twenty-ninth", "thirtieth",
    "thirty-first",
)


def _num_to_words(n: int, lang: str) -> str:
    """Wrapper around num2words that falls back to digits-as-string when
    the language isn't supported or the lib isn't importable."""
    try:
        from num2words import num2words  # type: ignore
    except ImportError:
        return str(n)
    try:
        return num2words(n, lang=lang)
    except (NotImplementedError, Exception):  # noqa: BLE001
        try:
            return num2words(n, lang="en")
        except Exception:  # noqa: BLE001
            return str(n)


def _expand_dates(text: str, lang: str) -> str:
    is_pt = lang == "pt"
    def _r(m: re.Match[str]) -> str:
        d, mo = int(m.group("d")), int(m.group("m"))
        y_s = m.group("y")
        # Disambiguate DD/MM vs MM/DD: PT-BR is always DD/MM; for EN we
        # prefer MM/DD unless the first field is > 12 (then it has to be
        # DD/MM).
        if is_pt:
            day, month = d, mo
        else:
            if d > 12 and mo <= 12:
                day, month = d, mo
            else:
                day, month = mo, d
        if not (1 <= month <= 12) or not (1 <= day <= 31):
            return m.group(0)  # not a real date, leave it
        if is_pt:
            phrase = f"{_num_to_words(day, 'pt_BR')} de {_PT_MONTHS[month - 1]}"
            if y_s:
                year = int(y_s) if len(y_s) == 4 else 2000 + int(y_s)
                phrase += f" de {_num_to_words(year, 'pt_BR')}"
            return phrase
        # English
        ordinal = (
            _EN_ORDINALS[day - 1] if 1 <= day <= len(_EN_ORDINALS) else str(day)
        )
        phrase = f"{_EN_MONTHS[month - 1]} {ordinal}"
        if y_s:
            year = int(y_s) if len(y_s) == 4 else 2000 + int(y_s)
            phrase += f", {_num_to_words(year, 'en')}"
        return phrase
    return _DATE_RE.sub(_r, text)


def _expand_times(text: str, lang: str) -> str:
    is_pt = lang == "pt"
    def _r(m: re.Match[str]) -> str:
        h, mins = int(m.group("h")), int(m.group("min"))
        if not (0 <= h < 24) or not (0 <= mins < 60):
            return m.group(0)
        if is_pt:
            h_words = _num_to_words(h, "pt_BR")
            if mins == 0:
                return f"{h_words} horas"
            return f"{h_words} e {_num_to_words(mins, 'pt_BR')}"
        # English: 24h → 12h with am/pm
        suffix = "AM" if h < 12 else "PM"
        h12 = h % 12 or 12
        return f"{h12}:{mins:02d} {suffix}" if mins else f"{h12} {suffix}"
    return _TIME_RE.sub(_r, text)


def _expand_numbers(text: str, lang: str) -> str:
    """Expand digit runs into their spoken form. Keeps 0–9 as digits
    (Piper reads them fine), expands ≥10."""
    target_lang = "pt_BR" if lang == "pt" else "en"
    def _r(m: re.Match[str]) -> str:
        whole_raw = m.group("n")
        # Strip thousand separators. PT uses '.', EN uses ','.
        clean_int = re.sub(r"[.,]", "", whole_raw)
        try:
            n = int(clean_int)
        except ValueError:
            return m.group(0)
        if abs(n) < 10 and not m.group("frac"):
            return m.group(0)  # single digits read fine as-is
        whole_spoken = _num_to_words(n, target_lang)
        frac_raw = m.group("frac")
        if not frac_raw:
            return whole_spoken
        # Decimals: read each digit individually so 3.14 → "três vírgula
        # um quatro" / "three point one four" (matches how people say
        # version numbers and irrational constants).
        sep = "vírgula" if lang == "pt" else "point"
        frac_spoken = " ".join(
            _num_to_words(int(d), target_lang) for d in frac_raw
        )
        return f"{whole_spoken} {sep} {frac_spoken}"
    return _NUMBER_RE.sub(_r, text)


# ── Public entry point ─────────────────────────────────────────────────────


def normalize_for_speech(text: str, lang: str | None = None) -> str:
    """Apply every transformation in the right order. ``lang`` is a
    2-letter ISO code; when None or unrecognized, only language-agnostic
    rules apply.
    """
    if not text:
        return ""
    L = (lang or "").lower().split("-")[0]
    # Fenced code + tables BEFORE any other regex touches them — they have
    # delimiters our other passes would chew through.
    text = _replace_fenced(text, L)
    text = _replace_tables(text, L)
    text = _strip_markdown(text)
    text = _strip_emojis(text)
    text = _replace_urls(text)
    # Hashes / UUIDs run AFTER url replacement (so we don't try to "hash"
    # the path of a URL we already collapsed) and BEFORE number expansion
    # (so num2words doesn't try to spell out a 32-digit hex string).
    text = _replace_hashes(text, L)
    if L in ("en", "pt"):
        text = _expand_dates(text, L)
        text = _expand_times(text, L)
        text = _expand_numbers(text, L)
    text = _collapse_whitespace(text)
    return text


__all__: Iterable[str] = ("normalize_for_speech",)
