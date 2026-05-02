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
  s = s.replace(/^---\n[\s\S]*?\n---\n?/m, "");

  // HTML comments (used by Nexus for nx:* markers).
  s = s.replace(/<!--[\s\S]*?-->/g, "");

  // Fenced code blocks — replace with a short spoken placeholder so the
  // listener doesn't wonder if we silently dropped something. Mermaid
  // gets a "diagram" announcement; everything else gets "code block".
  s = s.replace(/```(\w*)\s*\n[\s\S]*?\n```/g, (_m, lang) => {
    const l = String(lang || "").toLowerCase();
    if (l === "mermaid") return "(a diagram follows)";
    if (!l || l === "text") return "(a code block follows)";
    return `(a ${l} code block follows)`;
  });

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

  // Horizontal rules
  s = s.replace(/^[-*_]{3,}\s*$/gm, "");

  // Any stray pipes left over from non-table contexts.
  s = s.replace(/\|/g, " ");

  // Collapse whitespace.
  s = s.replace(/\n{3,}/g, "\n\n").trim();

  return s;
}
