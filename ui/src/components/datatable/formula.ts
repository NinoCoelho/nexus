/**
 * Tiny safe formula evaluator for data-table `formula` fields.
 *
 * Supported: numeric literals, string literals, field-name identifiers
 * resolved against the row dict, parens, and operators + - * / %.
 * Unary minus is allowed. No function calls, no property access, no
 * keywords — anything else aborts with NaN/empty so a malformed formula
 * never throws.
 */

const TOKEN_RE = /\s*(?:(\d+\.?\d*|\.\d+)|([A-Za-z_][A-Za-z0-9_]*)|("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')|([+\-*/%()]))/y;

type Token =
  | { type: "num"; value: number }
  | { type: "str"; value: string }
  | { type: "id"; name: string }
  | { type: "op"; op: string };

function tokenize(src: string): Token[] | null {
  const out: Token[] = [];
  TOKEN_RE.lastIndex = 0;
  let pos = 0;
  while (pos < src.length) {
    TOKEN_RE.lastIndex = pos;
    const m = TOKEN_RE.exec(src);
    if (!m || m.index !== pos) {
      // skip whitespace-only tail
      const tail = src.slice(pos).trim();
      if (tail === "") break;
      return null;
    }
    pos = TOKEN_RE.lastIndex;
    if (m[1] !== undefined) out.push({ type: "num", value: parseFloat(m[1]) });
    else if (m[2] !== undefined) out.push({ type: "id", name: m[2] });
    else if (m[3] !== undefined) {
      const raw = m[3].slice(1, -1).replace(/\\(.)/g, "$1");
      out.push({ type: "str", value: raw });
    } else if (m[4] !== undefined) out.push({ type: "op", op: m[4] });
  }
  return out;
}

interface Parser {
  toks: Token[];
  i: number;
}

function peek(p: Parser): Token | undefined { return p.toks[p.i]; }
function eat(p: Parser): Token | undefined { return p.toks[p.i++]; }

// expr   = term (('+' | '-') term)*
// term   = factor (('*' | '/' | '%') factor)*
// factor = '-' factor | atom
// atom   = number | string | id | '(' expr ')'
function parseExpr(p: Parser, row: Record<string, unknown>): number | string {
  let lhs = parseTerm(p, row);
  for (;;) {
    const t = peek(p);
    if (t?.type === "op" && (t.op === "+" || t.op === "-")) {
      eat(p);
      const rhs = parseTerm(p, row);
      if (t.op === "+") {
        if (typeof lhs === "string" || typeof rhs === "string") {
          lhs = String(lhs) + String(rhs);
        } else {
          lhs = lhs + rhs;
        }
      } else {
        lhs = toNum(lhs) - toNum(rhs);
      }
    } else break;
  }
  return lhs;
}

function parseTerm(p: Parser, row: Record<string, unknown>): number | string {
  let lhs: number | string = parseFactor(p, row);
  for (;;) {
    const t = peek(p);
    if (t?.type === "op" && (t.op === "*" || t.op === "/" || t.op === "%")) {
      eat(p);
      const rhs = toNum(parseFactor(p, row));
      const ln = toNum(lhs);
      if (t.op === "*") lhs = ln * rhs;
      else if (t.op === "/") lhs = rhs === 0 ? NaN : ln / rhs;
      else lhs = rhs === 0 ? NaN : ln % rhs;
    } else break;
  }
  return lhs;
}

function parseFactor(p: Parser, row: Record<string, unknown>): number | string {
  const t = peek(p);
  if (t?.type === "op" && t.op === "-") {
    eat(p);
    return -toNum(parseFactor(p, row));
  }
  return parseAtom(p, row);
}

function parseAtom(p: Parser, row: Record<string, unknown>): number | string {
  const t = eat(p);
  if (!t) throw new Error("unexpected end");
  if (t.type === "num") return t.value;
  if (t.type === "str") return t.value;
  if (t.type === "id") {
    const v = row[t.name];
    if (typeof v === "number") return v;
    if (typeof v === "string") {
      const asNum = parseFloat(v);
      if (!Number.isNaN(asNum) && /^-?\d+\.?\d*$/.test(v.trim())) return asNum;
      return v;
    }
    if (v == null) return 0;
    return Number(v) || 0;
  }
  if (t.type === "op" && t.op === "(") {
    const inner = parseExpr(p, row);
    const close = eat(p);
    if (!close || close.type !== "op" || close.op !== ")") throw new Error("expected )");
    return inner;
  }
  throw new Error(`unexpected token`);
}

function toNum(v: number | string): number {
  if (typeof v === "number") return v;
  const n = parseFloat(v);
  return Number.isNaN(n) ? 0 : n;
}

export function evalFormula(expr: string, row: Record<string, unknown>): unknown {
  if (!expr || !expr.trim()) return "";
  const toks = tokenize(expr);
  if (!toks || toks.length === 0) return "";
  try {
    const result = parseExpr({ toks, i: 0 }, row);
    if (typeof result === "number") {
      if (!Number.isFinite(result)) return "";
      // Trim trailing zeros from float results
      return Math.round(result * 1e6) / 1e6;
    }
    return result;
  } catch {
    return "";
  }
}
