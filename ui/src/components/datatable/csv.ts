/** Minimal CSV parse/serialize for vault data-table import/export. */

export function toCSV(headers: string[], rows: Record<string, unknown>[]): string {
  const lines: string[] = [headers.map(escape).join(",")];
  for (const row of rows) {
    lines.push(headers.map((h) => escape(formatValue(row[h]))).join(","));
  }
  return lines.join("\n");
}

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (Array.isArray(v)) return v.join("; ");
  return String(v);
}

function escape(s: string): string {
  if (/[",\n\r]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
  return s;
}

/** Parse CSV into header + row dicts. Handles quoted fields and escaped quotes. */
export function parseCSV(src: string): { headers: string[]; rows: Record<string, string>[] } {
  const records: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let inQuotes = false;
  let i = 0;
  while (i < src.length) {
    const ch = src[i];
    if (inQuotes) {
      if (ch === '"') {
        if (src[i + 1] === '"') { cell += '"'; i += 2; continue; }
        inQuotes = false; i++; continue;
      }
      cell += ch; i++; continue;
    }
    if (ch === '"') { inQuotes = true; i++; continue; }
    if (ch === ",") { row.push(cell); cell = ""; i++; continue; }
    if (ch === "\r") { i++; continue; }
    if (ch === "\n") { row.push(cell); records.push(row); row = []; cell = ""; i++; continue; }
    cell += ch; i++;
  }
  if (cell !== "" || row.length > 0) { row.push(cell); records.push(row); }
  if (records.length === 0) return { headers: [], rows: [] };
  const headers = records[0].map((h) => h.trim());
  const rows: Record<string, string>[] = [];
  for (let r = 1; r < records.length; r++) {
    const rec = records[r];
    if (rec.length === 1 && rec[0] === "") continue;
    const obj: Record<string, string> = {};
    headers.forEach((h, idx) => { obj[h] = rec[idx] ?? ""; });
    rows.push(obj);
  }
  return { headers, rows };
}

export function downloadCSV(filename: string, csv: string): void {
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
