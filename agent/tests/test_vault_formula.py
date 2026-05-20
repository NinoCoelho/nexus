"""Tests for the vault_formula module — safe formula evaluator."""

from nexus.vault_formula import eval_formula, validate_formula


def _n(v):
    """Normalize: numbers to float, keep strings."""
    if isinstance(v, (int, float)):
        return round(float(v) * 1e6) / 1e6
    return v


# ── Basic arithmetic ─────────────────────────────────────────────────────────


def test_add():
    assert _n(eval_formula("2 + 3", {})) == 5.0


def test_subtract():
    assert _n(eval_formula("10 - 4", {})) == 6.0


def test_multiply():
    assert _n(eval_formula("3 * 5", {})) == 15.0


def test_divide():
    assert _n(eval_formula("10 / 4", {})) == 2.5


def test_modulo():
    assert _n(eval_formula("10 % 3", {})) == 1.0


def test_divide_by_zero():
    assert eval_formula("10 / 0", {}) == ""


def test_precedence():
    assert _n(eval_formula("2 + 3 * 4", {})) == 14.0


def test_parens():
    assert _n(eval_formula("(2 + 3) * 4", {})) == 20.0


def test_unary_minus():
    assert _n(eval_formula("-5 + 3", {})) == -2.0


# ── Field references ─────────────────────────────────────────────────────────


def test_field_ref():
    assert _n(eval_formula("price * qty", {"price": 10, "qty": 3})) == 30.0


def test_missing_field_is_zero():
    assert _n(eval_formula("a + b", {"a": 5})) == 5.0


def test_string_field_numeric():
    assert _n(eval_formula("val * 2", {"val": "7"})) == 14.0


# ── String operations ────────────────────────────────────────────────────────


def test_string_concat():
    assert eval_formula('"hello" + " " + "world"', {}) == "hello world"


def test_field_string_concat():
    assert eval_formula('first + " " + last', {"first": "John", "last": "Doe"}) == "John Doe"


def test_string_literal_single_quotes():
    assert eval_formula("'hello'", {}) == "hello"


# ── Comparison operators ─────────────────────────────────────────────────────


def test_eq():
    assert _n(eval_formula("a == b", {"a": 5, "b": 5})) == 1.0


def test_neq():
    assert _n(eval_formula("a != b", {"a": 5, "b": 3})) == 1.0


def test_gt():
    assert _n(eval_formula("a > b", {"a": 10, "b": 5})) == 1.0


def test_lt():
    assert _n(eval_formula("a < b", {"a": 3, "b": 7})) == 1.0


def test_gte():
    assert _n(eval_formula("a >= b", {"a": 5, "b": 5})) == 1.0


def test_lte():
    assert _n(eval_formula("a <= b", {"a": 5, "b": 5})) == 1.0


def test_comparison_false():
    assert _n(eval_formula("5 > 10", {})) == 0.0


# ── Logical operators ────────────────────────────────────────────────────────


def test_and():
    assert _n(eval_formula("1 AND 1", {})) == 1.0


def test_and_false():
    assert _n(eval_formula("1 AND 0", {})) == 0.0


def test_or():
    assert _n(eval_formula("0 OR 1", {})) == 1.0


def test_or_false():
    assert _n(eval_formula("0 OR 0", {})) == 0.0


def test_not():
    assert _n(eval_formula("NOT 1", {})) == 0.0


def test_not_zero():
    assert _n(eval_formula("NOT 0", {})) == 1.0


def test_compound_logic():
    row = {"score": 85, "attendance": 90}
    assert _n(eval_formula("score > 80 AND attendance > 85", row)) == 1.0


# ── Functions ────────────────────────────────────────────────────────────────


def test_if_true():
    assert _n(eval_formula('IF(1, "yes", "no")', {})) == "yes"


def test_if_false():
    assert _n(eval_formula('IF(0, "yes", "no")', {})) == "no"


def test_if_comparison():
    row = {"score": 75}
    assert eval_formula('IF(score >= 60, "pass", "fail")', row) == "pass"


def test_round():
    assert _n(eval_formula("ROUND(3.14159, 2)", {})) == 3.14


def test_round_zero_digits():
    assert _n(eval_formula("ROUND(3.7, 0)", {})) == 4.0


def test_abs():
    assert _n(eval_formula("ABS(-5)", {})) == 5.0


def test_abs_positive():
    assert _n(eval_formula("ABS(5)", {})) == 5.0


def test_min():
    assert _n(eval_formula("MIN(3, 1, 4, 1, 5)", {})) == 1.0


def test_max():
    assert _n(eval_formula("MAX(3, 1, 4, 1, 5)", {})) == 5.0


def test_coalesce():
    assert eval_formula('COALESCE(empty_val, "fallback")', {"empty_val": ""}) == "fallback"


def test_coalesce_first_non_empty():
    assert _n(eval_formula("COALESCE(val, 42)", {"val": 7})) == 7.0


def test_coalesce_skips_empty_string():
    assert eval_formula('COALESCE("", "fallback")', {}) == "fallback"


def test_coalesce_first():
    assert eval_formula('COALESCE("first", "second")', {}) == "first"


def test_len():
    assert _n(eval_formula('LEN("hello")', {})) == 5.0


def test_concat():
    assert eval_formula('CONCAT("a", "-", "b")', {}) == "a-b"


def test_nested_functions():
    row = {"delta": -15, "threshold": 10}
    result = eval_formula('IF(ABS(delta) > threshold, "alert", "ok")', row)
    assert result == "alert"


def test_if_with_arithmetic():
    row = {"qty": 5, "price": 10}
    assert _n(eval_formula("IF(qty > 10, price * 0.9, price)", row)) == 10.0


# ── Null / error handling ────────────────────────────────────────────────────


def test_empty_expression():
    assert eval_formula("", {}) == ""
    assert eval_formula("   ", {}) == ""


def test_malformed_expression():
    assert eval_formula("+++", {}) == ""


def test_unmatched_parens():
    assert eval_formula("(2 + 3", {}) == ""


def test_none_result():
    assert eval_formula("10 / 0", {}) == ""


# ── Rollup filter usage ──────────────────────────────────────────────────────


def test_filter_eq_string():
    row = {"status": "active"}
    assert _n(eval_formula('status == "active"', row)) == 1.0


def test_filter_neq_string():
    row = {"status": "cancelled"}
    assert _n(eval_formula('status == "active"', row)) == 0.0


def test_filter_compound():
    row = {"status": "active", "qty": 5}
    assert _n(eval_formula('status == "active" AND qty > 0', row)) == 1.0


def test_filter_compound_false():
    row = {"status": "cancelled", "qty": 5}
    assert _n(eval_formula('status == "active" AND qty > 0', row)) == 0.0


# ── validate_formula ─────────────────────────────────────────────────────────


def test_validate_good():
    assert validate_formula("price * qty") == []


def test_validate_if():
    assert validate_formula('IF(x > 0, "pos", "neg")') == []


def test_validate_empty():
    assert len(validate_formula("")) > 0


def test_validate_bad_syntax():
    assert len(validate_formula("+++")) > 0


def test_validate_unmatched_parens():
    assert len(validate_formula("(2 + 3")) > 0
