export { default as BarChartViz } from "./BarChartViz";
export { default as LineChartViz } from "./LineChartViz";
export { default as AreaChartViz } from "./AreaChartViz";
export { default as PieChartViz } from "./PieChartViz";
export { default as TableViz } from "./TableViz";
export { default as KpiCardViz } from "./KpiCardViz";
export { default as ChartTooltip } from "./ChartTooltip";
export { CHART_PALETTE, formatNumber, formatValue } from "./palette";
export type { VizType, VizConfig, QueryResult, VizProps } from "./types";

import type { VizType, VizProps } from "./types";
import type { ComponentType } from "react";
import BarChartViz from "./BarChartViz";
import LineChartViz from "./LineChartViz";
import AreaChartViz from "./AreaChartViz";
import PieChartViz from "./PieChartViz";
import TableViz from "./TableViz";
import KpiCardViz from "./KpiCardViz";

const VIZ_COMPONENTS: Record<VizType, ComponentType<VizProps>> = {
  bar: BarChartViz,
  line: LineChartViz,
  area: AreaChartViz,
  pie: PieChartViz,
  donut: PieChartViz,
  table: TableViz,
  kpi: KpiCardViz,
};

export function getVizComponent(vizType: VizType) {
  return VIZ_COMPONENTS[vizType] ?? TableViz;
}
