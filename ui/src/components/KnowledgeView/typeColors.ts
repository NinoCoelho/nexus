// Shared entity type → color map used by SubgraphCanvas3D, EntityTypeFilter, and utils.

export const TYPE_COLORS: Record<string, string> = {
  person: "#c9a84c",
  project: "#b87333",
  concept: "#7a5e9e",
  technology: "#5e7a9e",
  decision: "#9e4a3a",
  resource: "#4a9e7a",
};

export const DEFAULT_TYPE_COLOR = "#7a9e7e";

export function typeColor(t: string): string {
  return TYPE_COLORS[t] ?? DEFAULT_TYPE_COLOR;
}
