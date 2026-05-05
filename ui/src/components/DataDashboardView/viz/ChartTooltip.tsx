import { formatValue } from "./palette";

interface TooltipPayloadEntry {
  name: string;
  value: number;
  color: string;
  dataKey: string;
}

interface CustomTooltipProps {
  active?: boolean;
  payload?: TooltipPayloadEntry[];
  label?: string;
  numberFormat?: string;
}

export default function ChartTooltip({ active, payload, label, numberFormat }: CustomTooltipProps) {
  if (!active || !payload?.length) return null;

  return (
    <div
      style={{
        background: "var(--bg-panel)",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius)",
        padding: "8px 12px",
        fontSize: 12,
        color: "var(--fg)",
        boxShadow: "0 2px 8px rgba(0,0,0,0.15)",
      }}
    >
      {label != null && (
        <div style={{ fontWeight: 600, marginBottom: 4, color: "var(--fg-dim)" }}>{label}</div>
      )}
      {payload.map((entry) => (
        <div key={entry.dataKey} style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span
            style={{
              width: 8,
              height: 8,
              borderRadius: "50%",
              background: entry.color,
              flexShrink: 0,
            }}
          />
          <span style={{ color: "var(--fg-dim)" }}>{entry.name}:</span>
          <span style={{ fontWeight: 600 }}>{formatValue(entry.value, numberFormat)}</span>
        </div>
      ))}
    </div>
  );
}

export function createTooltipProps(numberFormat?: string): Record<string, unknown> {
  return { numberFormat };
}
