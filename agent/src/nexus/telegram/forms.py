"""Form-prompt rendering + reply parsing for Telegram HITL answers.

``ask_user(kind='form')`` prompts forwarded to Telegram render two things:
a numbered field reference and a **copyable fill-in template** (a ``<pre>``
block of ``Key:`` lines). The intended flow: copy the template, fill the
gaps, send it back. The parser is template-tolerant:

- ``Key:`` with a blank value or an untouched placeholder (``[…]``,
  ``<…>``, the field's ``placeholder`` text, ``…``) counts as *unfilled* —
  it never leaks into positional parsing;
- named lines switch the reply into named mode: stray lines error out
  instead of silently filling the next field;
- replies without any named line still work positionally (values in field
  order), and single-field forms take the whole message.

Forms containing ``secret: true`` fields are never answerable from
Telegram (``has_secret_fields`` gates the forwarder): typing a password
into a Telegram chat would store it in Telegram's cloud history, which
the web flow deliberately avoids.
"""

from __future__ import annotations

import html
import re
from typing import Any

# "field: value" / "field=value" — the key side is word-ish (unicode, so
# accented labels like "Cidade" work) and deliberately short so prose
# containing colons falls through to positional parsing instead of being
# misread as a field key. The value side may be EMPTY — that's a
# template gap the user left unfilled.
_NAME_VALUE_RE = re.compile(r"^\s*(\w[\w ./\-]{0,63}?)\s*[:=]\s*(.*)$")
_BULLET_RE = re.compile(r"^[\s\-•*>]*(?:\d+[.)]\s*)?")
# Values wrapped like this were pasted from the template untouched.
_PLACEHOLDER_WRAP_RE = re.compile(r"^(?:\[.*\]|<.*>|\{.*\})$")

_BOOL_TRUE = {"true", "yes", "y", "1", "on"}
_BOOL_FALSE = {"false", "no", "n", "0", "off"}


def has_secret_fields(fields: list[dict[str, Any]] | None) -> bool:
    return any(bool(f.get("secret")) for f in (fields or []))


def single_choice_field(fields: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """The field when the form is exactly one boolean/select — the
    reply-keyboard case (allowed values become tappable buttons)."""
    if not fields or len(fields) != 1:
        return None
    f = fields[0]
    if f.get("kind") == "boolean":
        return f
    if f.get("kind") == "select" and f.get("choices"):
        return f
    return None


def choice_values(field: dict[str, Any]) -> list[str]:
    if field.get("kind") == "boolean":
        return ["yes", "no"]
    return [str(c) for c in (field.get("choices") or [])]


def _template_key(field: dict[str, Any]) -> str:
    """The key a template line uses: the label when it is copy-safe
    (no colon, short), else the field name. Both are parser-matchable."""
    label = str(field.get("label") or "").strip()
    name = str(field.get("name") or "")
    if label and ":" not in label and "\n" not in label and len(label) <= 48:
        return label
    return name


def render_form_prompt(
    fields: list[dict[str, Any]],
    title: str | None = None,
    description: str | None = None,
) -> str:
    """Render the field reference + fill-in template for the bubble (HTML)."""
    lines: list[str] = []
    if title:
        lines.append(f"<b>{html.escape(str(title))}</b>")
    if description:
        lines.append(html.escape(str(description)))
    for i, f in enumerate(fields, 1):
        name = str(f.get("name", ""))
        req = " *" if f.get("required") else ""
        line = f"{i}. <code>{html.escape(name)}</code>{req}"
        label = f.get("label")
        if label and str(label) != name:
            line += f" — {html.escape(str(label))}"
        kind = f.get("kind", "text")
        if kind == "boolean":
            line += " (yes/no)"
        elif kind == "select":
            line += f" (one of: {html.escape(', '.join(choice_values(f)))})"
        elif kind == "multiselect":
            line += f" (comma-separated from: {html.escape(', '.join(choice_values(f)))})"
        elif kind == "number":
            line += " (number)"
        if f.get("help"):
            line += f" — {html.escape(str(f['help']))}"
        lines.append(line)

    # Copyable fill-in template: tap-copy the block, fill the gaps, send.
    # A field's `placeholder` rides along in brackets as the gap hint —
    # the parser treats untouched bracketed values as unfilled.
    template: list[str] = []
    for f in fields:
        key = html.escape(_template_key(f))
        ph = f.get("placeholder")
        template.append(f"{key}: [{html.escape(str(ph))}]" if ph else f"{key}:")
    lines.append("📋 Copy, fill the gaps, send back:")
    lines.append("<pre>" + "\n".join(template) + "</pre>")
    return "\n".join(lines)


def format_answer_for_display(answer: Any) -> str:
    """One-line summary of a parsed answer for the ✅ bubble edit."""
    if isinstance(answer, dict):
        return " · ".join(f"{k}: {v}" for k, v in answer.items())
    return str(answer)


def parse_form_reply(
    text: str, fields: list[dict[str, Any]]
) -> tuple[dict[str, Any] | None, list[str]]:
    """Parse a Telegram reply into a form answer.

    Returns ``(answer, errors)`` — exactly one is populated. Accepted
    shapes: a filled-in template (``Key: value`` lines; blank gaps and
    untouched placeholders count as unfilled), bare values in field
    order, or — for single-field forms — the whole message. Once any
    named line is recognized, stray lines error out instead of being
    positionally crammed onto another field.
    """
    errors: list[str] = []
    lookup = _field_lookup(fields)
    answers: dict[str, Any] = {}
    unmatched: list[str] = []
    named_seen = False

    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    for ln in lines:
        stripped = _BULLET_RE.sub("", ln).strip()
        m = _NAME_VALUE_RE.match(stripped)
        if m:
            field = _match_field(m.group(1), lookup)
            if field is not None:
                named_seen = True
                value = m.group(2).strip()
                # Template gap left unfilled (blank value or untouched
                # placeholder): the field stays unanswered — the
                # required-missing pass below names it if it matters.
                if value and not _is_placeholder(value, field):
                    _assign(field, value, answers, errors)
                continue
        unmatched.append(ln)

    # Single-field form: the whole reply is the value (bare value or a
    # multi-line text field). Only when no named match happened.
    if len(fields) == 1 and not named_seen and not answers and not errors:
        _assign(fields[0], (text or "").strip(), answers, errors)
        unmatched.clear()

    if unmatched:
        if named_seen:
            # Mixed reply: named lines plus strays. Never guess a field
            # for a stray — it may be a multi-line continuation that
            # would silently land on the wrong field.
            for leftover in unmatched:
                errors.append(f"Couldn't match: “{leftover[:80]}”")
        else:
            # Bare values fill unfilled fields in declared order.
            unfilled = [f for f in fields if f.get("name") not in answers]
            for f in unfilled:
                if not unmatched:
                    break
                _assign(f, unmatched.pop(0), answers, errors)
            for leftover in unmatched:
                errors.append(f"Couldn't match: “{leftover[:80]}”")

    for f in fields:
        name = f.get("name")
        if name in answers:
            continue
        if f.get("required"):
            errors.append(f"Missing required field: {name}")
        elif f.get("default") is not None:
            answers[name] = f.get("default")

    if errors:
        return None, errors
    return answers, []


def _is_placeholder(value: str, field: dict[str, Any]) -> bool:
    """True when a value was pasted from the template untouched."""
    v = value.strip()
    if _PLACEHOLDER_WRAP_RE.match(v):
        return True
    if v in {"…", "..."}:
        return True
    ph = field.get("placeholder")
    if ph and v.lower() == str(ph).strip().lower():
        return True
    return False


def _field_lookup(fields: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for f in fields:
        name = str(f.get("name", "")).strip().lower()
        if name:
            lookup.setdefault(name, f)
        label = str(f.get("label") or "").strip().lower()
        if label:
            lookup.setdefault(label, f)
    return lookup


def _match_field(
    key: str, lookup: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    k = key.strip().lower()
    if not k:
        return None
    if k in lookup:
        return lookup[k]
    norm = lambda s: re.sub(r"[\s_-]+", "", s)  # noqa: E731
    kn = norm(k)
    for cand, f in lookup.items():
        if norm(cand) == kn:
            return f
    if len(kn) >= 3:
        for cand, f in lookup.items():
            cn = norm(cand)
            if cn.startswith(kn) or kn.startswith(cn):
                return f
    return None


def _assign(
    field: dict[str, Any], raw: str, answers: dict[str, Any], errors: list[str]
) -> None:
    name = str(field.get("name"))
    if name in answers:
        errors.append(f"Field “{name}” given twice")
        return
    if not (raw or "").strip():
        errors.append(f"Empty value for “{name}”")
        return
    value, err = _coerce(field, raw.strip())
    if err is not None:
        errors.append(err)
    else:
        answers[name] = value


def _coerce(field: dict[str, Any], value: str) -> tuple[Any, str | None]:
    name = str(field.get("name"))
    kind = field.get("kind", "text")
    if kind == "number":
        for conv in (int, float):
            try:
                return conv(value), None
            except ValueError:
                continue
        return None, f"“{name}” must be a number"
    if kind == "boolean":
        v = value.strip().lower()
        if v in _BOOL_TRUE:
            return True, None
        if v in _BOOL_FALSE:
            return False, None
        return None, f"“{name}” must be yes/no"
    if kind in ("select", "multiselect"):
        choices = [str(c) for c in (field.get("choices") or [])]
        if not choices:
            return value, None
        if kind == "select":
            for c in choices:
                if value == c or value.lower() == c.lower():
                    return c, None
            return None, f"“{name}” must be one of: {', '.join(choices)}"
        picked: list[str] = []
        for part in re.split(r"[,;]", value):
            p = part.strip()
            if not p:
                continue
            for c in choices:
                if p == c or p.lower() == c.lower():
                    picked.append(c)
                    break
            else:
                return None, f"“{name}” has invalid entry “{p}” (allowed: {', '.join(choices)})"
        return picked, None
    return value, None
