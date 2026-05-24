import {
  BarChart,
  Bar,
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

export default function BarChartViz({ rows, config, height = 280 }: VizProps) {
  if (!rows.length) return <EmptyState />;

  const xField = config.x_field ?? "";
  const yFields = config.y_fields ?? (config.y_field ? [config.y_field] : []);
  if (!yFields.length) return <EmptyState />;

  const horizontal = config.horizontal === true;
  const showGrid = config.show_grid !== false;

  const CategoryAxis = horizontal ? YAxis : XAxis;
  const ValueAxis = horizontal ? XAxis : YAxis;

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart
        data={rows}
        layout={horizontal ? "vertical" : "horizontal"}
        margin={{ top: 8, right: 16, left: 8, bottom: 8 }}
      >
        {showGrid && <CartesianGrid strokeDasharray="3 3" stroke="var(--border-soft)" />}
        <CategoryAxis
          dataKey={xField}
          tick={{ fontSize: 11, fill: "var(--fg-dim)" }}
          axisLine={{ stroke: "var(--border-soft)" }}
          tickLine={{ stroke: "var(--border-soft)" }}
          type="category"
        />
        <ValueAxis
          tick={{ fontSize: 11, fill: "var(--fg-dim)" }}
          axisLine={{ stroke: "var(--border-soft)" }}
          tickLine={{ stroke: "var(--border-soft)" }}
          label={
            config.y_label
              ? { value: config.y_label, position: "insideLeft", angle: -90, style: { fontSize: 11, fill: "var(--fg-dim)" } }
              : undefined
          }
          type="number"
        />
        <Tooltip
          content={<ChartTooltip numberFormat={config.number_format as string | undefined} />}
          cursor={{ fill: "var(--bg-hover)", opacity: 0.5 }}
        />
        {yFields.length > 1 && <Legend wrapperStyle={{ fontSize: 11, color: "var(--fg-dim)" }} />}
        {yFields.map((field, i) => (
          <Bar
            key={field}
            dataKey={field}
            fill={CHART_PALETTE[i % CHART_PALETTE.length]}
            stackId={config.stacked ? "stack" : undefined}
            radius={[2, 2, 0, 0]}
          />
        ))}
      </BarChart>
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
