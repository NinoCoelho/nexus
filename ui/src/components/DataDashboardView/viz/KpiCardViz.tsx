import type { VizProps } from "./types";
import { formatNumber, formatValue } from "./palette";

export default function KpiCardViz({ rows, config }: VizProps) {
  const yField = config.y_field ?? "";
  const labelField = config.label_field ?? config.x_field ?? "";
  const trendField = config.trend_field ?? "";
  const numFormat = config.number_format as string | undefined;

  const firstRow = rows[0];
  if (!firstRow) {
    return (
      <div
        style={{
          height: 100,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          color: "var(--fg-dim)",
          fontSize: 13,
        }}
      >
        No data
      </div>
    );
  }

  const mainValue = firstRow[yField];
  const numValue = typeof mainValue === "number" ? mainValue : parseFloat(String(mainValue ?? ""));
  const displayValue = Number.isNaN(numValue) ? String(mainValue ?? "—") : formatValue(numValue, numFormat);

  const label = String(firstRow[labelField] ?? config.title ?? yField);

  let trend: "up" | "down" | null = null;
  let trendDelta = 0;
  if (trendField && rows.length >= 2) {
    const prev = Number(rows[1][trendField] ?? 0);
    const curr = Number(rows[0][trendField] ?? 0);
    trendDelta = curr - prev;
    if (trendDelta > 0) trend = "up";
    else if (trendDelta < 0) trend = "down";
  }

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 4,
        padding: "12px 16px",
        background: "var(--bg-panel)",
        borderRadius: "var(--radius)",
        border: "1px solid var(--border-soft)",
      }}
    >
      <div style={{ fontSize: 12, color: "var(--fg-dim)", fontWeight: 500 }}>{label}</div>
      <div
        style={{
          fontSize: 28,
          fontWeight: 700,
          color: "var(--fg)",
          lineHeight: 1.1,
          letterSpacing: "-0.02em",
        }}
      >
        {displayValue}
      </div>
      {trend && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 4,
            fontSize: 12,
            fontWeight: 600,
            color: trend === "up" ? "var(--ok)" : "var(--bad)",
          }}
        >
          <span>{trend === "up" ? "↑" : "↓"}</span>
          <span>{formatNumber(Math.abs(trendDelta))}</span>
        </div>
      )}
    </div>
  );
}
