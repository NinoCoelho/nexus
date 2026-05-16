import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts";
import type { VizProps } from "./types";
import { CHART_PALETTE } from "./palette";
import ChartTooltip from "./ChartTooltip";

export default function AreaChartViz({ rows, config, height = 280 }: VizProps) {
  if (!rows.length) return <EmptyState />;

  const xField = config.x_field ?? "";
  const yFields = config.y_fields ?? (config.y_field ? [config.y_field] : []);
  if (!yFields.length) return <EmptyState />;

  const showGrid = config.show_grid !== false;

  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={rows} margin={{ top: 8, right: 16, left: 8, bottom: 8 }}>
        {showGrid && <CartesianGrid strokeDasharray="3 3" stroke="var(--border-soft)" />}
        <XAxis
          dataKey={xField}
          tick={{ fontSize: 11, fill: "var(--fg-dim)" }}
          axisLine={{ stroke: "var(--border-soft)" }}
          tickLine={{ stroke: "var(--border-soft)" }}
        />
        <YAxis
          tick={{ fontSize: 11, fill: "var(--fg-dim)" }}
          axisLine={{ stroke: "var(--border-soft)" }}
          tickLine={{ stroke: "var(--border-soft)" }}
          label={
            config.y_label
              ? { value: config.y_label, position: "insideLeft", angle: -90, style: { fontSize: 11, fill: "var(--fg-dim)" } }
              : undefined
          }
        />
        <Tooltip
          content={<ChartTooltip numberFormat={config.number_format as string | undefined} />}
        />
        {yFields.length > 1 && <Legend wrapperStyle={{ fontSize: 11, color: "var(--fg-dim)" }} />}
        {yFields.map((field, i) => {
          const color = CHART_PALETTE[i % CHART_PALETTE.length];
          return (
            <Area
              key={field}
              type="monotone"
              dataKey={field}
              stroke={color}
              fill={color}
              fillOpacity={0.15}
              stackId={config.stacked ? "stack" : undefined}
              strokeWidth={2}
            />
          );
        })}
      </AreaChart>
    </ResponsiveContainer>
  );
}

function EmptyState() {
  return (
    <div
      style={{
        height: 280,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        color: "var(--fg-dim)",
        fontSize: 13,
      }}
    >
      No data to display
    </div>
  );
}
