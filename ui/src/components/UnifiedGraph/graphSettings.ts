export interface GraphSettings {
  nodeSize: number;
  linkDistance: number;
  chargeStrength: number;
  linkWidth: number;
  linkCurvature: number;
  linkOpacity: number;
  nodeOpacity: number;
  labelScale: number;
  particleSpeed: number;
  particleWidth: number;
}

export const DEFAULT_GRAPH_SETTINGS: GraphSettings = {
  nodeSize: 1,
  linkDistance: 1,
  chargeStrength: 1,
  linkWidth: 0.4,
  linkCurvature: 0.1,
  linkOpacity: 0.5,
  nodeOpacity: 0.9,
  labelScale: 1,
  particleSpeed: 0.005,
  particleWidth: 1.2,
};

const STORAGE_KEY = "nexus-graph-settings";

export function loadGraphSettings(): GraphSettings {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULT_GRAPH_SETTINGS };
    const parsed = JSON.parse(raw);
    return { ...DEFAULT_GRAPH_SETTINGS, ...parsed };
  } catch {
    return { ...DEFAULT_GRAPH_SETTINGS };
  }
}

export function saveGraphSettings(settings: GraphSettings): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
  } catch { /* ignore */ }
}

export interface GraphSettingsField {
  key: keyof GraphSettings;
  label: string;
  min: number;
  max: number;
  step: number;
  format: (v: number) => string;
}

export const GRAPH_SETTINGS_FIELDS: GraphSettingsField[] = [
  { key: "nodeSize", label: "Node Size", min: 0.2, max: 3, step: 0.1, format: (v) => `${v.toFixed(1)}×` },
  { key: "linkDistance", label: "Link Distance", min: 0.3, max: 3, step: 0.1, format: (v) => `${v.toFixed(1)}×` },
  { key: "chargeStrength", label: "Repulsion", min: 0.3, max: 3, step: 0.1, format: (v) => `${v.toFixed(1)}×` },
  { key: "linkWidth", label: "Link Width", min: 0.1, max: 2, step: 0.1, format: (v) => v.toFixed(1) },
  { key: "linkCurvature", label: "Link Curvature", min: 0, max: 0.5, step: 0.02, format: (v) => v.toFixed(2) },
  { key: "linkOpacity", label: "Link Opacity", min: 0.1, max: 1, step: 0.05, format: (v) => `${Math.round(v * 100)}%` },
  { key: "nodeOpacity", label: "Node Opacity", min: 0.2, max: 1, step: 0.05, format: (v) => `${Math.round(v * 100)}%` },
  { key: "labelScale", label: "Label Size", min: 0.3, max: 3, step: 0.1, format: (v) => `${v.toFixed(1)}×` },
  { key: "particleSpeed", label: "Particle Speed", min: 0.001, max: 0.03, step: 0.001, format: (v) => v.toFixed(3) },
  { key: "particleWidth", label: "Particle Size", min: 0.2, max: 3, step: 0.1, format: (v) => v.toFixed(1) },
];
