"""Telegram form-reply parsing — ``nexus.telegram.forms``.

Covers the accepted reply shapes (``name: value`` lines, positional
values, bare single-field values), coercion rules (number / boolean /
select / multiselect), fuzzy field matching, defaults, and the secret +
single-choice helpers the forwarder gates on.
"""

from __future__ import annotations

from nexus.telegram.forms import (
    choice_values,
    format_answer_for_display,
    has_secret_fields,
    parse_form_reply,
    render_form_prompt,
    single_choice_field,
)

FIELDS = [
    {"name": "city", "label": "City", "kind": "text", "required": True},
    {"name": "days", "kind": "number", "required": True},
    {"name": "notes", "kind": "textarea"},
]


def test_named_lines_with_labels_and_bullets() -> None:
    answer, errors = parse_form_reply(
        "- City: Lisbon\n2. days: 3\nnotes: bring umbrella", FIELDS
    )
    assert errors == []
    assert answer == {"city": "Lisbon", "days": 3, "notes": "bring umbrella"}


def test_positional_values_in_field_order() -> None:
    answer, errors = parse_form_reply("Lisbon\n7", FIELDS)
    assert errors == []
    assert answer == {"city": "Lisbon", "days": 7}


def test_fuzzy_name_matching() -> None:
    answer, errors = parse_form_reply("CITY: Porto\nDays = 2", FIELDS)
    assert errors == []
    assert answer == {"city": "Porto", "days": 2}


def test_optional_default_filled() -> None:
    fields = [
        {"name": "name", "kind": "text", "required": True},
        {"name": "retries", "kind": "number", "default": 3},
    ]
    answer, errors = parse_form_reply("name: x", fields)
    assert errors == []
    assert answer == {"name": "x", "retries": 3}


def test_missing_required_reported() -> None:
    answer, errors = parse_form_reply("days: 3", FIELDS)
    assert answer is None
    assert any("city" in e for e in errors)


def test_number_coercion_error() -> None:
    answer, errors = parse_form_reply("city: x\ndays: three", FIELDS)
    assert answer is None
    assert any("number" in e for e in errors)


def test_boolean_field() -> None:
    fields = [{"name": "loud", "kind": "boolean"}]
    answer, _ = parse_form_reply("loud: YES", fields)
    assert answer == {"loud": True}
    answer, errors = parse_form_reply("loud: maybe", fields)
    assert answer is None and any("yes/no" in e for e in errors)


def test_select_membership() -> None:
    fields = [{"name": "mode", "kind": "select", "choices": ["Fast", "Slow"]}]
    answer, _ = parse_form_reply("mode: fast", fields)
    assert answer == {"mode": "Fast"}
    answer, errors = parse_form_reply("mode: Medium", fields)
    assert answer is None and any("one of" in e for e in errors)


def test_multiselect_csv() -> None:
    fields = [
        {"name": "tags", "kind": "multiselect", "choices": ["a", "b", "c"]}
    ]
    answer, _ = parse_form_reply("tags: a, c", fields)
    assert answer == {"tags": ["a", "c"]}
    answer, errors = parse_form_reply("tags: a, z", fields)
    assert answer is None and any("invalid entry" in e for e in errors)


def test_single_field_bare_value_whole_text() -> None:
    fields = [{"name": "query", "kind": "text", "required": True}]
    answer, _ = parse_form_reply("search: anything with colons", fields)
    assert answer == {"query": "search: anything with colons"}


def test_multiline_single_textarea() -> None:
    fields = [{"name": "body", "kind": "textarea"}]
    answer, _ = parse_form_reply("line one\nline two", fields)
    assert answer == {"body": "line one\nline two"}


def test_duplicate_field_rejected() -> None:
    answer, errors = parse_form_reply("city: a\ncity: b\ndays: 1", FIELDS)
    assert answer is None
    assert any("twice" in e for e in errors)


def test_unmatched_lines_reported() -> None:
    # More bare lines than fields → the leftover can't be mapped.
    answer, errors = parse_form_reply("city: a\ndays: 1\nfiller\nextra junk", FIELDS)
    assert answer is None
    assert any("extra junk" in e for e in errors)


def test_secret_helpers() -> None:
    assert has_secret_fields([{"name": "a"}, {"name": "b", "secret": True}])
    assert not has_secret_fields([{"name": "a"}])
    assert not has_secret_fields(None)


def test_single_choice_field_helper() -> None:
    single = [{"name": "m", "kind": "select", "choices": ["x"]}]
    assert single_choice_field(single) is single[0]
    boolean = [{"name": "b", "kind": "boolean"}]
    assert single_choice_field(boolean) is boolean[0]
    assert single_choice_field(FIELDS) is None
    assert single_choice_field([]) is None
    assert choice_values({"kind": "boolean"}) == ["yes", "no"]
    assert choice_values({"kind": "select", "choices": ["x", "y"]}) == ["x", "y"]


def test_render_form_prompt_lists_fields() -> None:
    out = render_form_prompt(FIELDS, title="Trip", description="Fill it")
    assert "<b>Trip</b>" in out
    assert "Fill it" in out
    assert "city" in out and "days" in out
    assert "*" in out  # required marker


def test_render_includes_copyable_template() -> None:
    out = render_form_prompt(FIELDS, title="Trip")
    assert "📋" in out and "<pre>" in out and "</pre>" in out
    tmpl = out.split("<pre>", 1)[1].split("</pre>", 1)[0]
    # Template keys use the label when copy-safe, else the name.
    assert "City:" in tmpl and "days:" in tmpl and "notes:" in tmpl


def test_render_template_uses_placeholder_hint() -> None:
    fields = [{"name": "city", "kind": "text", "placeholder": "Where to"}]
    out = render_form_prompt(fields)
    assert "city: [Where to]" in out


def test_template_blank_gap_is_unfilled_not_positional() -> None:
    # The key regression: a pasted template with an optional gap left
    # blank must NOT leak into positional parsing.
    answer, errors = parse_form_reply("city: Lisbon\ndays: 3\nnotes:", FIELDS)
    assert errors == []
    assert answer == {"city": "Lisbon", "days": 3}


def test_template_placeholder_untouched_is_unfilled() -> None:
    answer, errors = parse_form_reply("city: [City]\ndays: 3", FIELDS)
    assert answer is None
    assert any("city" in e for e in errors)


def test_template_angle_bracket_placeholder_untouched() -> None:
    answer, errors = parse_form_reply("city: <City>\ndays: 1", FIELDS)
    assert answer is None
    assert any("city" in e for e in errors)


def test_field_placeholder_attribute_untouched() -> None:
    fields = [
        {"name": "city", "kind": "text", "required": True, "placeholder": "Where to"},
        {"name": "days", "kind": "number", "required": True},
    ]
    # Filled-in placeholder line: value differs from the hint.
    answer, _ = parse_form_reply("city: Porto\ndays: 2", fields)
    assert answer == {"city": "Porto", "days": 2}
    # Untouched paste of the hint itself counts as unfilled.
    answer, errors = parse_form_reply("city: Where to\ndays: 2", fields)
    assert answer is None
    assert any("city" in e for e in errors)


def test_mixed_named_and_stray_lines_error_not_misfill() -> None:
    # Stray lines after named ones must error, never silently fill
    # another field.
    answer, errors = parse_form_reply("city: Lisbon\nrandom junk", FIELDS)
    assert answer is None
    assert any("random junk" in e for e in errors)
    assert any("days" in e for e in errors)  # required still missing


def test_unicode_label_key_matches() -> None:
    fields = [
        {"name": "city", "label": "Cidade", "kind": "text", "required": True},
        {"name": "days", "kind": "number", "required": True},
    ]
    answer, errors = parse_form_reply("Cidade: Lisboa\ndays: 4", fields)
    assert errors == []
    assert answer == {"city": "Lisboa", "days": 4}


def test_format_answer_display() -> None:
    assert format_answer_for_display({"a": 1, "b": "x"}) == "a: 1 · b: x"
    assert format_answer_for_display("plain") == "plain"
