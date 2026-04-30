/**
 * Client-side mirror of the high-precision prefix patterns from
 * `agent/src/nexus/redact.py`.
 *
 * This is intentionally a curated subset — the goal is the user input bar's
 * "looks like a credential, are you sure?" guard, not log-line redaction.
 * Patterns that produce false positives on plain English (env-var assignments,
 * phone numbers, JSON field names) are deliberately excluded; the server's
 * `redact_sensitive_text` handles those for log lines.
 *
 * If you add a pattern here, add the equivalent regex to
 * `_PREFIX_PATTERNS` in redact.py — the test
 * `test_secret_patterns_parity.py` enforces parity.
 */

export interface SecretPattern {
  name: string;
  regex: RegExp;
}

export const SECRET_PATTERNS: SecretPattern[] = [
  { name: "openai", regex: /sk-[A-Za-z0-9_-]{10,}/g },
  { name: "github_pat_classic", regex: /ghp_[A-Za-z0-9]{10,}/g },
  { name: "github_pat_finegrained", regex: /github_pat_[A-Za-z0-9_]{10,}/g },
  { name: "github_oauth", regex: /gho_[A-Za-z0-9]{10,}/g },
  { name: "github_user_to_server", regex: /ghu_[A-Za-z0-9]{10,}/g },
  { name: "github_server_to_server", regex: /ghs_[A-Za-z0-9]{10,}/g },
  { name: "github_refresh", regex: /ghr_[A-Za-z0-9]{10,}/g },
  { name: "slack", regex: /xox[baprs]-[A-Za-z0-9-]{10,}/g },
  { name: "google_api", regex: /AIza[A-Za-z0-9_-]{30,}/g },
  { name: "perplexity", regex: /pplx-[A-Za-z0-9]{10,}/g },
  { name: "aws_access_key", regex: /AKIA[A-Z0-9]{16}/g },
  { name: "stripe_secret_live", regex: /sk_live_[A-Za-z0-9]{10,}/g },
  { name: "stripe_secret_test", regex: /sk_test_[A-Za-z0-9]{10,}/g },
  { name: "stripe_restricted_live", regex: /rk_live_[A-Za-z0-9]{10,}/g },
  { name: "sendgrid", regex: /SG\.[A-Za-z0-9_-]{10,}/g },
  { name: "huggingface", regex: /hf_[A-Za-z0-9]{10,}/g },
  { name: "replicate", regex: /r8_[A-Za-z0-9]{10,}/g },
  { name: "npm_token", regex: /npm_[A-Za-z0-9]{10,}/g },
  { name: "pypi_token", regex: /pypi-[A-Za-z0-9_-]{10,}/g },
  { name: "digitalocean_pat", regex: /dop_v1_[A-Za-z0-9]{10,}/g },
  { name: "tavily", regex: /tvly-[A-Za-z0-9]{10,}/g },
  { name: "exa", regex: /exa_[A-Za-z0-9]{10,}/g },
  { name: "groq", regex: /gsk_[A-Za-z0-9]{10,}/g },
  { name: "matrix_access", regex: /syt_[A-Za-z0-9]{10,}/g },
  { name: "jwt", regex: /eyJ[A-Za-z0-9_-]{10,}(?:\.[A-Za-z0-9_=-]{4,}){0,2}/g },
  {
    name: "private_key_block",
    regex: /-----BEGIN[A-Z ]*PRIVATE KEY-----[\s\S]*?-----END[A-Z ]*PRIVATE KEY-----/g,
  },
];

/**
 * Shannon-entropy fallback for unknown formats. We only flag tokens that are
 * (a) long enough to plausibly carry a secret (≥ 24 chars), (b) made of
 * URL-safe / base64-ish chars, and (c) high enough entropy to be unlikely
 * natural language (> 4.0 bits/char). The threshold is calibrated so plain
 * English prose, file paths, and version strings don't trip it.
 */
const HIGH_ENTROPY_TOKEN_RE = /[A-Za-z0-9_+/=.-]{24,}/g;

function shannonEntropy(s: string): number {
  if (!s) return 0;
  const counts = new Map<string, number>();
  for (const ch of s) counts.set(ch, (counts.get(ch) ?? 0) + 1);
  const len = s.length;
  let h = 0;
  for (const c of counts.values()) {
    const p = c / len;
    h -= p * Math.log2(p);
  }
  return h;
}

export interface SecretMatch {
  /** Pattern name (e.g. "openai"), or "high_entropy" for the entropy fallback. */
  reason: string;
  /** Index of the first matched character in the input. */
  start: number;
  /** Index just past the matched substring. */
  end: number;
  /** The matched substring. */
  value: string;
}

/**
 * Scan `text` for credential-shaped substrings. Returns all matches sorted by
 * start offset; an empty array means nothing tripped the heuristics.
 */
export function findSecrets(text: string): SecretMatch[] {
  if (!text) return [];
  const out: SecretMatch[] = [];
  for (const { name, regex } of SECRET_PATTERNS) {
    regex.lastIndex = 0;
    let m: RegExpExecArray | null;
    while ((m = regex.exec(text)) !== null) {
      out.push({ reason: name, start: m.index, end: m.index + m[0].length, value: m[0] });
    }
  }
  // Entropy fallback. Skip ranges already covered by a prefix match.
  HIGH_ENTROPY_TOKEN_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = HIGH_ENTROPY_TOKEN_RE.exec(text)) !== null) {
    const start = m.index;
    const end = start + m[0].length;
    const overlaps = out.some((s) => start < s.end && s.start < end);
    if (overlaps) continue;
    if (shannonEntropy(m[0]) > 4.0) {
      out.push({ reason: "high_entropy", start, end, value: m[0] });
    }
  }
  out.sort((a, b) => a.start - b.start);
  return out;
}

/** Convenience: true when at least one secret was found. */
export function looksLikeSecret(text: string): boolean {
  return findSecrets(text).length > 0;
}
