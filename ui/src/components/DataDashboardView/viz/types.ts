export type VizType = "bar" | "line" | "area" | "pie" | "donut" | "table" | "kpi";

export interface VizConfig {
  x_field?: string;
  y_field?: string;
  y_fields?: string[];
  y_label?: string;
  x_label?: string;
  title?: string;
  stacked?: boolean;
  horizontal?: boolean;
  show_dots?: boolean;
  show_grid?: boolean;
  donut?: boolean;
  number_format?: string;
  trend_field?: string;
  label_field?: string;
  [key: string]: unknown;
}

export interface QueryResult {
  columns: { name: string; type: string }[];
  rows: Record<string, unknown>[];
  row_count: number;
}

export interface VizProps {
  columns: { name: string; type: string }[];
  rows: Record<string, unknown>[];
  config: VizConfig;
  width?: number;
  height?: number;
}
