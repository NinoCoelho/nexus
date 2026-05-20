"""Safe formula evaluator for data-table computed fields.

Supported
---------
- Arithmetic: ``+ - * / %``
- Comparisons: ``== != > < >= <=`` (return 1 or 0)
- Logical: ``AND OR NOT``
- Functions: ``IF ROUND ABS MIN MAX COALESCE LEN CONCAT``
- Field references resolved against a row dict
- String literals in ``"double"`` or ``'single'`` quotes
- Parenthesised sub-expressions

Malformed expressions never raise — ``eval_formula`` returns ``""``.
"""

from __future__ import annotations

import math
import re
from typing import Any

_TOKEN_RE = re.compile(
    r"\s*(?:"
    r"(\d+\.?\d*|\.\d+)"          # group 1: number
    r"|([A-Za-z_]\w*)"                                # group 2: identifier
    r'|((?:\"(?:[^"\\]|\\.)*\"|\'(?:[^\'\\]|\\.)*\'))'  # group 3: string (double or single)
    r"|([+\-*/%(),]|==|!=|>=|<=|[><]))"                 # group 4: operator
)

_FUNCTIONS: dict[str, Any] | None = None


def _get_functions() -> dict[str, Any]:
    global _FUNCTIONS
    if _FUNCTIONS is not None:
        return _FUNCTIONS

    def _if(args: list) -> Any:
        return args[1] if _truthy(args[0]) else args[2] if len(args) > 2 else ""

    def _round(args: list) -> float:
        digits = _to_num(args[1]) if len(args) > 1 else 0
        return round(_to_num(args[0]), int(digits))

    def _abs(args: list) -> float:
        return abs(_to_num(args[0]))

    def _min(args: list) -> float:
        return min(_to_num(a) for a in args)

    def _max(args: list) -> float:
        return max(_to_num(a) for a in args)

    def _coalesce(args: list) -> Any:
        for a in args:
            if a is not None and a != "":
                return a
        return ""

    def _len(args: list) -> int:
        return len(str(args[0] if args[0] is not None else ""))

    def _concat(args: list) -> str:
        return "".join(str(a) if a is not None else "" for a in args)

    _FUNCTIONS = {
        "IF": _if,
        "ROUND": _round,
        "ABS": _abs,
        "MIN": _min,
        "MAX": _max,
        "COALESCE": _coalesce,
        "LEN": _len,
        "CONCAT": _concat,
    }
    return _FUNCTIONS


def _to_num(v: Any) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    if v is None or v == "":
        return 0.0
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0.0


def _truthy(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v != ""
    return bool(v)


def _round_result(v: float) -> float | str:
    if not math.isfinite(v):
        return ""
    return round(v * 1_000_000) / 1_000_000


def _resolve_field(row: dict[str, Any], name: str) -> Any:
    v = row.get(name)
    if v is None:
        return 0
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        try:
            n = float(v)
            if re.match(r"^-?\d+\.?\d*$", v.strip()):
                return n
        except ValueError:
            pass
        return v
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0


class _Token:
    __slots__ = ("kind", "value")

    def __init__(self, kind: str, value: Any) -> None:
        self.kind = kind
        self.value = value


def _tokenize(src: str) -> list[_Token] | None:
    tokens: list[_Token] = []
    pos = 0
    while pos < len(src):
        m = _TOKEN_RE.match(src, pos)
        if not m:
            if src[pos:].strip() == "":
                break
            return None
        pos = m.end()
        if m.group(1) is not None:
            tokens.append(_Token("num", float(m.group(1))))
        elif m.group(2) is not None:
            tokens.append(_Token("id", m.group(2)))
        elif m.group(3) is not None:
            raw = m.group(3)
            s = raw[1:-1].replace("\\\"", '"').replace("\\'", "'").replace("\\\\", "\\")
            tokens.append(_Token("str", s))
        elif m.group(4) is not None:
            tokens.append(_Token("op", m.group(4)))
    return tokens


class _Parser:
    __slots__ = ("tokens", "pos")

    def __init__(self, tokens: list[_Token]) -> None:
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> _Token | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def eat(self) -> _Token | None:
        t = self.peek()
        if t is not None:
            self.pos += 1
        return t

    def expect_op(self, op: str) -> bool:
        t = self.peek()
        if t and t.kind == "op" and t.value == op:
            self.pos += 1
            return True
        return False


class _FormulaError(Exception):
    pass


def _parse_or(p: _Parser, row: dict[str, Any]) -> Any:
    lhs = _parse_and(p, row)
    while True:
        t = p.peek()
        if t and t.kind == "id" and t.value == "OR":
            p.eat()
            rhs = _parse_and(p, row)
            if _truthy(lhs) or _truthy(rhs):
                lhs = 1
            else:
                lhs = 0
        else:
            break
    return lhs


def _parse_and(p: _Parser, row: dict[str, Any]) -> Any:
    lhs = _parse_comparison(p, row)
    while True:
        t = p.peek()
        if t and t.kind == "id" and t.value == "AND":
            p.eat()
            rhs = _parse_comparison(p, row)
            if _truthy(lhs) and _truthy(rhs):
                lhs = 1
            else:
                lhs = 0
        else:
            break
    return lhs


_CMP_OPS = {
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
}


def _parse_comparison(p: _Parser, row: dict[str, Any]) -> Any:
    lhs = _parse_addition(p, row)
    t = p.peek()
    if t and t.kind == "op" and t.value in _CMP_OPS:
        op = t.value
        p.eat()
        rhs = _parse_addition(p, row)
        ln = _to_num(lhs) if not isinstance(lhs, str) else lhs
        rn = _to_num(rhs) if not isinstance(rhs, str) else rhs
        return 1 if _CMP_OPS[op](ln, rn) else 0
    return lhs


def _parse_addition(p: _Parser, row: dict[str, Any]) -> Any:
    lhs = _parse_multiplication(p, row)
    while True:
        t = p.peek()
        if t and t.kind == "op" and t.value in ("+", "-"):
            op = t.value
            p.eat()
            rhs = _parse_multiplication(p, row)
            if op == "+":
                if isinstance(lhs, str) or isinstance(rhs, str):
                    lhs = str(lhs) + str(rhs)
                else:
                    lhs = _to_num(lhs) + _to_num(rhs)
            else:
                lhs = _to_num(lhs) - _to_num(rhs)
        else:
            break
    return lhs


def _parse_multiplication(p: _Parser, row: dict[str, Any]) -> Any:
    lhs = _parse_unary(p, row)
    while True:
        t = p.peek()
        if t and t.kind == "op" and t.value in ("*", "/", "%"):
            op = t.value
            p.eat()
            rhs = _to_num(_parse_unary(p, row))
            ln = _to_num(lhs)
            if op == "*":
                lhs = ln * rhs
            elif op == "/":
                lhs = float("nan") if rhs == 0 else ln / rhs
            else:
                lhs = float("nan") if rhs == 0 else ln % rhs
        else:
            break
    return lhs


def _parse_unary(p: _Parser, row: dict[str, Any]) -> Any:
    t = p.peek()
    if t and t.kind == "op" and t.value == "-":
        p.eat()
        return -_to_num(_parse_unary(p, row))
    if t and t.kind == "id" and t.value == "NOT":
        p.eat()
        val = _parse_unary(p, row)
        return 0 if _truthy(val) else 1
    return _parse_call(p, row)


def _parse_call(p: _Parser, row: dict[str, Any]) -> Any:
    t = p.peek()
    if t and t.kind == "id":
        name = t.value
        funcs = _get_functions()
        if name.upper() in funcs:
            next_t = p.tokens[p.pos + 1] if p.pos + 1 < len(p.tokens) else None
            if next_t and next_t.kind == "op" and next_t.value == "(":
                p.eat()
                p.eat()
                args: list[Any] = []
                arg_t = p.peek()
                if arg_t and not (arg_t.kind == "op" and arg_t.value == ")"):
                    args.append(_parse_or(p, row))
                    while True:
                        ct = p.peek()
                        if ct and ct.kind == "op" and ct.value == ",":
                            p.eat()
                            args.append(_parse_or(p, row))
                        else:
                            break
                close = p.peek()
                if not (close and close.kind == "op" and close.value == ")"):
                    raise _FormulaError("expected )")
                p.eat()
                try:
                    return funcs[name.upper()](args)
                except Exception:
                    raise _FormulaError(f"function {name}() error")
    return _parse_atom(p, row)


def _parse_atom(p: _Parser, row: dict[str, Any]) -> Any:
    t = p.eat()
    if t is None:
        raise _FormulaError("unexpected end of expression")
    if t.kind == "num":
        return t.value
    if t.kind == "str":
        return t.value
    if t.kind == "id":
        return _resolve_field(row, t.value)
    if t.kind == "op" and t.value == "(":
        inner = _parse_or(p, row)
        close = p.peek()
        if not (close and close.kind == "op" and close.value == ")"):
            raise _FormulaError("expected )")
        p.eat()
        return inner
    raise _FormulaError(f"unexpected token: {t.kind}={t.value!r}")


def eval_formula(expr: str, row: dict[str, Any]) -> Any:
    """Evaluate a formula expression against a row dict.

    Returns a number, string, or ``""`` on error. Never raises.
    """
    if not expr or not expr.strip():
        return ""
    tokens = _tokenize(expr)
    if not tokens:
        return ""
    try:
        p = _Parser(tokens)
        result = _parse_or(p, row)
        if isinstance(result, (int, float)):
            r = _round_result(float(result))
            return r
        return result
    except _FormulaError:
        return ""
    except Exception:
        return ""


def validate_formula(expr: str) -> list[str]:
    """Try to parse *expr*. Return a list of error strings (empty = valid)."""
    if not expr or not expr.strip():
        return ["empty expression"]
    tokens = _tokenize(expr)
    if tokens is None:
        return ["syntax error in expression"]
    try:
        p = _Parser(tokens)
        _parse_or(p, {})
        if p.pos < len(p.tokens):
            return [f"unexpected token at position {p.pos}"]
    except _FormulaError as exc:
        return [str(exc)]
    except Exception as exc:
        return [str(exc)]
    return []
