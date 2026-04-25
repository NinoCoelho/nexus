// Pure color and geometry helpers for GraphView.

const PALETTE = [
  "#b87333", "#7a9e7e", "#c9a84c", "#9e4a3a",
  "#5e7a9e", "#7a5e9e", "#9e7a5e", "#4a9e7a",
];

const TYPE_COLORS: Record<string, string> = {
  person: "#c9a84c", project: "#b87333", concept: "#7a5e9e",
  technology: "#5e7a9e", decision: "#9e4a3a", resource: "#4a9e7a",
};

export function folderColor(folder: string): string {
  if (!folder) return PALETTE[0];
  let h = 0;
  for (let i = 0; i < folder.length; i++) h = (h * 31 + folder.charCodeAt(i)) >>> 0;
  return PALETTE[h % PALETTE.length];
}

export function entityColor(type: string): string {
  return TYPE_COLORS[type] ?? "#7a9e7e";
}

export function nodeRadius(size: number): number {
  const r = Math.log(Math.max(size, 1) + 1) * 1.8;
  return Math.max(4, Math.min(14, r));
}
