import {
  LineChart,
  Line,
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

export default function LineChartViz({ rows, config, height = 280 }: VizProps) {
  if (!rows.length) return <EmptyState />;

  const xField = config.x_field ?? "";
  const yFields = config.y_fields ?? (config.y_field ? [config.y_field] : []);
  if (!yFields.length) return <EmptyState />;

  const showDots = config.show_dots !== false;
  const showGrid = config.show_grid !== false;

  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={rows} margin={{ top: 8, right: 16, left: 8, bottom: 8 }}>
        {showGrid && <CartesianGrid strokeDasharray="3 3" stroke="var(--border-soft)" />}
        <XAxis
          dataKey={xField}
          tick={{ fontSize: 11, fill: "var(--fg-dim)" }}
          axisLine={{ stroke: "var(--border-soft)" }}
          tickLine={{ stroke: "var(--border-soft)" }}
          label={
            config.x_label
              ? { value: config.x_label, position: "insideBottom", offset: -4, style: { fontSize: 11, fill: "var(--fg-dim)" } }
              : undefined
          }
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
        {yFields.map((field, i) => (
          <Line
            key={field}
            type="monotone"
            dataKey={field}
            stroke={CHART_PALETTE[i % CHART_PALETTE.length]}
            strokeWidth={2}
            dot={showDots ? { r: 3, fill: CHART_PALETTE[i % CHART_PALETTE.length] } : false}
            activeDot={{ r: 4 }}
          />
        ))}
      </LineChart>
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
