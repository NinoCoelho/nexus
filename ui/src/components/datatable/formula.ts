/**
 * Safe formula evaluator for data-table `formula` fields.
 *
 * Supported: numeric/string literals, field-name identifiers resolved against
 * the row dict, parentheses, arithmetic (+ - * / %), comparisons
 * (== != > < >= <= → 1|0), logical (AND, OR, NOT), and functions:
 * IF, ROUND, ABS, MIN, MAX, COALESCE, LEN, CONCAT.
 *
 * Malformed formulas never throw — they return `""`.
 */

const TOKEN_RE =
  /\s*(?:(\d+\.?\d*|\.\d+)|([A-Za-z_]\w*)|("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')|([+\-*/%(),]|==|!=|>=|<=|[><]))/y;

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

function peek(p: Parser): Token | undefined {
  return p.toks[p.i];
}
function eat(p: Parser): Token | undefined {
  return p.toks[p.i++];
}

type Val = number | string;

function toNum(v: Val): number {
  if (typeof v === "number") return v;
  const n = parseFloat(v);
  return Number.isNaN(n) ? 0 : n;
}

function truthy(v: Val): boolean {
  if (v == null) return false;
  if (typeof v === "number") return v !== 0;
  return v !== "";
}

// ── Function dispatch ────────────────────────────────────────────────────────

type Fn = (args: Val[]) => Val;

const FUNCTIONS: Record<string, Fn> = {
  IF(args) {
    return truthy(args[0]) ? args[1] : args.length > 2 ? args[2] : "";
  },
  ROUND(args) {
    const digits = toNum(args[1] ?? 0);
    const factor = 10 ** digits;
    return Math.round(toNum(args[0]) * factor) / factor;
  },
  ABS(args) {
    return Math.abs(toNum(args[0]));
  },
  MIN(args) {
    return Math.min(...args.map(toNum));
  },
  MAX(args) {
    return Math.max(...args.map(toNum));
  },
  COALESCE(args) {
    for (const a of args) {
      if (a != null && a !== "") return a;
    }
    return "";
  },
  LEN(args) {
    return String(args[0] ?? "").length;
  },
  CONCAT(args) {
    return args.map((a) => String(a ?? "")).join("");
  },
};

// ── Grammar (precedence low → high) ─────────────────────────────────────────
//
// or       = and ("OR" and)*
// and      = cmp ("AND" cmp)*
// cmp      = add (("=="|"!="|">"|"<"|">="|"<=") add)?
// add      = mul (("+"|"-") mul)*
// mul      = unary (("*"|"/"|"%") unary)*
// unary    = "-" unary | "NOT" unary | call
// call     = IDENT "(" args ")" | atom
// atom     = NUMBER | STRING | IDENT | "(" or ")"

function parseOr(p: Parser, row: Record<string, unknown>): Val {
  let lhs: Val = parseAnd(p, row);
  for (;;) {
    const t = peek(p);
    if (t?.type === "id" && t.name === "OR") {
      eat(p);
      const rhs = parseAnd(p, row);
      lhs = truthy(lhs) || truthy(rhs) ? 1 : 0;
    } else break;
  }
  return lhs;
}

function parseAnd(p: Parser, row: Record<string, unknown>): Val {
  let lhs: Val = parseCmp(p, row);
  for (;;) {
    const t = peek(p);
    if (t?.type === "id" && t.name === "AND") {
      eat(p);
      const rhs = parseCmp(p, row);
      lhs = truthy(lhs) && truthy(rhs) ? 1 : 0;
    } else break;
  }
  return lhs;
}

function parseCmp(p: Parser, row: Record<string, unknown>): Val {
  const lhs: Val = parseAdd(p, row);
  const t = peek(p);
  if (t?.type === "op" && ["==", "!=", ">", "<", ">=", "<="].includes(t.op)) {
    const op = t.op;
    eat(p);
    const rhs: Val = parseAdd(p, row);
    const ln = typeof lhs === "string" ? lhs : toNum(lhs);
    const rn = typeof rhs === "string" ? rhs : toNum(rhs);
    switch (op) {
      case "==": return ln === rn ? 1 : 0;
      case "!=": return ln !== rn ? 1 : 0;
      case ">":  return (ln as number) > (rn as number) ? 1 : 0;
      case "<":  return (ln as number) < (rn as number) ? 1 : 0;
      case ">=": return (ln as number) >= (rn as number) ? 1 : 0;
      case "<=": return (ln as number) <= (rn as number) ? 1 : 0;
    }
  }
  return lhs;
}

function parseAdd(p: Parser, row: Record<string, unknown>): Val {
  let lhs: Val = parseMul(p, row);
  for (;;) {
    const t = peek(p);
    if (t?.type === "op" && (t.op === "+" || t.op === "-")) {
      eat(p);
      const rhs = parseMul(p, row);
      if (t.op === "+") {
        if (typeof lhs === "string" || typeof rhs === "string") {
          lhs = String(lhs) + String(rhs);
        } else {
          lhs = toNum(lhs) + toNum(rhs);
        }
      } else {
        lhs = toNum(lhs) - toNum(rhs);
      }
    } else break;
  }
  return lhs;
}

function parseMul(p: Parser, row: Record<string, unknown>): Val {
  let lhs: Val = parseUnary(p, row);
  for (;;) {
    const t = peek(p);
    if (t?.type === "op" && (t.op === "*" || t.op === "/" || t.op === "%")) {
      eat(p);
      const rhs = toNum(parseUnary(p, row));
      const ln = toNum(lhs);
      if (t.op === "*") lhs = ln * rhs;
      else if (t.op === "/") lhs = rhs === 0 ? NaN : ln / rhs;
      else lhs = rhs === 0 ? NaN : ln % rhs;
    } else break;
  }
  return lhs;
}

function parseUnary(p: Parser, row: Record<string, unknown>): Val {
  const t = peek(p);
  if (t?.type === "op" && t.op === "-") {
    eat(p);
    return -toNum(parseUnary(p, row));
  }
  if (t?.type === "id" && t.name === "NOT") {
    eat(p);
    return truthy(parseUnary(p, row)) ? 0 : 1;
  }
  return parseCall(p, row);
}

function parseCall(p: Parser, row: Record<string, unknown>): Val {
  const t = peek(p);
  if (t?.type === "id") {
    const fn = FUNCTIONS[t.name.toUpperCase()];
    if (fn) {
      const next = p.toks[p.i + 1];
      if (next?.type === "op" && next.op === "(") {
        eat(p); // id
        eat(p); // (
        const args: Val[] = [];
        const first = peek(p);
        if (first && !(first.type === "op" && first.op === ")")) {
          args.push(parseOr(p, row));
          for (;;) {
            const ct = peek(p);
            if (ct?.type === "op" && ct.op === ",") {
              eat(p);
              args.push(parseOr(p, row));
            } else break;
          }
        }
        const close = eat(p);
        if (!close || close.type !== "op" || close.op !== ")")
          throw new Error("expected )");
        try {
          return fn(args);
        } catch {
          throw new Error(`function ${t.name}() error`);
        }
      }
    }
  }
  return parseAtom(p, row);
}

function parseAtom(p: Parser, row: Record<string, unknown>): Val {
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
    const inner = parseOr(p, row);
    const close = eat(p);
    if (!close || close.type !== "op" || close.op !== ")") throw new Error("expected )");
    return inner;
  }
  throw new Error(`unexpected token`);
}

export function evalFormula(expr: string, row: Record<string, unknown>): unknown {
  if (!expr || !expr.trim()) return "";
  const toks = tokenize(expr);
  if (!toks || toks.length === 0) return "";
  try {
    const result = parseOr({ toks, i: 0 }, row);
    if (typeof result === "number") {
      if (!Number.isFinite(result)) return "";
      return Math.round(result * 1e6) / 1e6;
    }
    return result;
  } catch {
    return "";
  }
}
