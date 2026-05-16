export const CHART_PALETTE = [
  "#3b82f6",
  "#10b981",
  "#f59e0b",
  "#ef4444",
  "#8b5cf6",
  "#ec4899",
  "#14b8a6",
  "#f97316",
];

export function formatNumber(value: number): string {
  if (value == null || Number.isNaN(value)) return "—";
  const abs = Math.abs(value);
  if (abs >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(1)}B`;
  if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  if (Number.isInteger(value)) return value.toLocaleString();
  return value.toFixed(2);
}

export function formatValue(value: unknown, numberFormat?: string): string {
  if (value == null) return "—";
  if (typeof value !== "number") return String(value);
  if (numberFormat === "currency") return `$${formatNumber(value)}`;
  if (numberFormat === "percent") return `${value.toFixed(1)}%`;
  if (numberFormat === "compact") return formatNumber(value);
  return formatNumber(value);
}
