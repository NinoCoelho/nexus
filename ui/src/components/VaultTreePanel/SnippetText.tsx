// Sub-component for VaultTreePanel: renders a search snippet with <mark> highlights.

type SnippetSegment = { text: string; highlight: boolean };

function parseSnippet(snippet: string): SnippetSegment[] {
  const segments: SnippetSegment[] = [];
  const re = /<mark>(.*?)<\/mark>/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(snippet)) !== null) {
    if (m.index > last) segments.push({ text: snippet.slice(last, m.index), highlight: false });
    segments.push({ text: m[1], highlight: true });
    last = m.index + m[0].length;
  }
  if (last < snippet.length) segments.push({ text: snippet.slice(last), highlight: false });
  return segments;
}

export function SnippetText({ snippet }: { snippet: string }) {
  const segs = parseSnippet(snippet);
  return (
    <span>
      {segs.map((s, i) =>
        s.highlight
          ? <mark key={i}>{s.text}</mark>
          : <span key={i}>{s.text}</span>
      )}
    </span>
  );
}
