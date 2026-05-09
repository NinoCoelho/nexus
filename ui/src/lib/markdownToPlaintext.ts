/**
 * Strip markdown syntax for TTS so the synthesizer reads "Hello world"
 * instead of "asterisk asterisk Hello world asterisk asterisk".
 *
 * Intentionally regex-based and pragmatic, not a full CommonMark parser.
 * It handles the cases that show up in practice in chat replies and
 * vault notes: links, code fences, inline code, emphasis, headings,
 * lists, blockquotes, HTML comments (we use these for nx:id markers),
 * and YAML frontmatter.
 *
 * Tables and fenced code blocks are replaced with short spoken
 * descriptions so the listener knows something was there. The backend's
 * `tts/normalize.py` does the same thing on its end (covers ack
 * transcripts and any direct synth call); doing it here too means the
 * UI can decide whether the result was likely-empty before sending.
 */
export function markdownToPlaintext(input: string): string {
  let s = input;

  // YAML frontmatter at the very top of vault files.
  s = s.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/, "");

  // HTML comments (used by Nexus for nx:* markers).
  s = s.replace(/<!--[\s\S]*?-->/g, "");

  // Math blocks ($$...$$) → spoken placeholder.
  s = s.replace(/\$\$[\s\S]*?\$\$/g, "(an equation)");

  // Fenced code blocks — replace with a short spoken placeholder so the
  // listener doesn't wonder if we silently dropped something. Handles
  // both ``` and ~~~ delimiters, with or without trailing newline before
  // closing fence. Mermaid gets a "diagram" announcement.
  const _fenced = (delim: string) =>
    new RegExp(
      `${delim}(\\w*)[^\\n]*\\n[\\s\\S]*?\\n?${delim}`, "g",
    );
  for (const re of [_fenced("```"), _fenced("~~~")]) {
    s = s.replace(re, (_m, lang) => {
      const l = String(lang || "").toLowerCase();
      if (l === "mermaid") return "(a diagram follows)";
      if (!l || l === "text") return "(a code block follows)";
      return `(a ${l} code block follows)`;
    });
  }

  // Indented code blocks — 2+ consecutive lines starting with 4+ spaces
  // or a tab. Replace with a short placeholder so code symbols don't
  // leak into TTS.
  s = s.replace(
    /((?:^(?: {4}|\t)[^\n]*\n?){2,})/gm,
    "(a code block follows)",
  );

  // Markdown tables → spoken description with header cell names + row count.
  // Capture: header row, separator row, ≥1 body row.
  s = s.replace(
    /(?:^\|[^\n]+\|\n)\|[\s:|-]+\|\n(?:\|[^\n]+\|\n?)+/gm,
    (block) => {
      const rows = block
        .split("\n")
        .filter((r) => r.trim().startsWith("|"));
      if (rows.length < 2) return block;
      const headerCells = rows[0]
        .split("|")
        .map((c) => c.trim())
        .filter(Boolean);
      const bodyRows = Math.max(0, rows.length - 2);
      const cols = headerCells.join(", ") || "columns";
      return `(a table follows showing ${cols}, with ${bodyRows} rows)`;
    },
  );

  // Inline code → just the contents.
  s = s.replace(/`([^`]+)`/g, "$1");

  // Images: ![alt](url) → alt
  s = s.replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1");

  // Links: [text](url) → text
  s = s.replace(/\[([^\]]+)\]\([^)]*\)/g, "$1");

  // Reference-style links: [text][ref] → text
  s = s.replace(/\[([^\]]+)\]\[[^\]]*\]/g, "$1");

  // Bare URLs → "link to <domain>"
  s = s.replace(/https?:\/\/([^\s)\]<>"]+)/g, (_m, rest) => {
    const domain = rest.split("/")[0].split("?")[0].split("#")[0];
    return domain ? `link to ${domain}` : "link";
  });

  // Footnote references [^1] → remove.
  s = s.replace(/\[\^[^\]]+\]/g, "");
  // Footnote definitions (at start of line) → remove whole line.
  s = s.replace(/^\[\^[^\]]+\]:\s*[^\n]*\n?/gm, "");

  // Task list markers: - [x] / - [ ] → "done: " or "".
  s = s.replace(/^\s*[-*+]\s+\[[ xX]\]\s*/gm, (m) => {
    const checked = /\[[xX]\]/.test(m);
    return checked ? "done: " : "";
  });

  // Headings (#, ##, …) — drop the leading hashes.
  s = s.replace(/^#{1,6}\s+/gm, "");

  // Blockquotes
  s = s.replace(/^>\s?/gm, "");

  // List markers
  s = s.replace(/^\s*[-*+]\s+/gm, "");
  s = s.replace(/^\s*\d+\.\s+/gm, "");

  // Bold / italic / strike — keep inner text.
  s = s.replace(/\*\*([^*]+)\*\*/g, "$1");
  s = s.replace(/__([^_]+)__/g, "$1");
  s = s.replace(/\*([^*\n]+)\*/g, "$1");
  s = s.replace(/_([^_\n]+)_/g, "$1");
  s = s.replace(/~~([^~]+)~~/g, "$1");

  // Escape sequences (\* \# etc.) → just the char.
  s = s.replace(/\\([\\`*_{}[\]()#+\-.!~|>])/g, "$1");

  // Horizontal rules
  s = s.replace(/^[-*_]{3,}\s*$/gm, "");

  // Ellipsis (... or …) → period.
  s = s.replace(/\.{3,}|…/g, ".");

  // Double/triple slashes (//, ///) — code comments, file paths. URLs
  // already replaced above.
  s = s.replace(/\/\/+/g, " ");

  // Any stray pipes left over from non-table contexts.
  s = s.replace(/\|/g, " ");

  // Arrow symbols → "to" (kanban lanes, flow diagrams).
  s = s.replace(/\s*→\s*/g, " to ");

  // Collapse whitespace.
  s = s.replace(/\n{3,}/g, "\n\n").trim();

  return s;
}
