import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer } from "recharts";
import type { VizProps } from "./types";
import { CHART_PALETTE, formatValue } from "./palette";

interface LabelProps {
  cx?: number;
  cy?: number;
  midAngle?: number;
  innerRadius?: number;
  outerRadius?: number;
  percent?: number;
  name?: string;
}

function renderLabel({ cx = 0, cy = 0, midAngle = 0, outerRadius = 0, percent = 0, name = "" }: LabelProps) {
  if (percent < 0.05) return null;
  const RADIAN = Math.PI / 180;
  const radius = outerRadius + 20;
  const x = cx + radius * Math.cos(-midAngle * RADIAN);
  const y = cy + radius * Math.sin(-midAngle * RADIAN);
  return (
    <text
      x={x}
      y={y}
      fill="var(--fg)"
      textAnchor={x > cx ? "start" : "end"}
      dominantBaseline="central"
      fontSize={11}
    >
      {name} ({(percent * 100).toFixed(0)}%)
    </text>
  );
}

export default function PieChartViz({ rows, config, height = 280 }: VizProps) {
  if (!rows.length) return <EmptyState />;

  const xField = config.x_field ?? "";
  const yField = config.y_field ?? "";
  if (!xField && !yField) return <EmptyState />;

  const isDonut = config.donut === true;

  const data = rows.map((row) => ({
    name: String(row[xField] ?? ""),
    value: Number(row[yField] ?? 0),
  }));

  const outerR = Math.min(height - 40, 120);
  const innerR = isDonut ? outerR * 0.55 : 0;

  return (
    <ResponsiveContainer width="100%" height={height}>
      <PieChart margin={{ top: 8, right: 24, left: 24, bottom: 8 }}>
        <Pie
          data={data}
          cx="50%"
          cy="50%"
          innerRadius={innerR}
          outerRadius={outerR}
          dataKey="value"
          nameKey="name"
          label={renderLabel}
          labelLine={false}
          strokeWidth={1}
          stroke="var(--bg-panel)"
        >
          {data.map((_, i) => (
            <Cell key={i} fill={CHART_PALETTE[i % CHART_PALETTE.length]} />
          ))}
        </Pie>
        <Tooltip
          formatter={(value: unknown, name: unknown) => [formatValue(Number(value), config.number_format as string | undefined), String(name)]}
          contentStyle={{
            background: "var(--bg-panel)",
            border: "1px solid var(--border)",
            borderRadius: "var(--radius)",
            fontSize: 12,
            color: "var(--fg)",
          }}
        />
      </PieChart>
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
