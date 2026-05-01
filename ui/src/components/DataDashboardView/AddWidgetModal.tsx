/**
 * AddWidgetModal — small form to define or edit a dashboard widget.
 *
 * Widgets are LLM-populated artifacts pinned below the database tables.
 * Each widget has a kind (chart / report / kpi) that steers the agent's
 * output format on refresh, a prompt the user authors once, and a refresh
 * strategy (manual / daily on first open).
 *
 * Pass ``editing`` to switch into edit mode — fields are pre-populated and
 * submitting reuses the same widget id (the server upserts by id, so the
 * existing result file stays linked).
 */

import { useMemo, useState } from "react";
import type { DashboardWidget, WidgetKind, WidgetRefresh } from "../../api/dashboard";

interface Props {
  onSubmit: (w: DashboardWidget) => void | Promise<void>;
  onCancel: () => void;
  /** When set, the modal opens in edit mode pre-populated with this widget's
   *  fields and submits with the same id. The kind picker is disabled in
   *  edit mode — switching kinds mid-life would orphan the saved result body. */
  editing?: DashboardWidget | null;
}

function slug(s: string): string {
  return s
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 60);
}

const KIND_BLURB: Record<WidgetKind, string> = {
  chart: "Chart — agent returns one nexus-chart fence (bar / line / pie).",
  report: "Report — terse markdown (bullets, paragraphs, lists, small tables).",
  kpi: "KPI — a single number with a one-line label.",
};

const KIND_PLACEHOLDER: Record<WidgetKind, string> = {
  chart: "Plot monthly revenue from sales.md as a bar chart.",
  report: "Summarize the open tickets, grouped by priority.",
  kpi: "How many customers signed up this month?",
};

export default function AddWidgetModal({ onSubmit, onCancel, editing }: Props) {
  const isEdit = !!editing;
  const [title, setTitle] = useState(editing?.title ?? "");
  const [kind, setKind] = useState<WidgetKind>(editing?.kind ?? "chart");
  const [prompt, setPrompt] = useState(editing?.prompt ?? "");
  const [refresh, setRefresh] = useState<WidgetRefresh>(editing?.refresh ?? "daily");
  const [saving, setSaving] = useState(false);

  const id = useMemo(() => editing?.id ?? `w_${slug(title || "widget")}`, [editing, title]);
  const canSubmit = title.trim().length > 0 && prompt.trim().length > 0;

  async function handleSubmit() {
    if (!canSubmit) return;
    setSaving(true);
    try {
      const widget: DashboardWidget = {
        id,
        kind,
        title: title.trim(),
        prompt: prompt.trim(),
        refresh,
        // Preserve the existing timestamp on edit so the daily-refresh
        // calculation doesn't immediately re-fire just because the user
        // changed the title.
        last_refreshed_at: editing?.last_refreshed_at ?? null,
        ...(editing?.order !== undefined ? { order: editing.order } : {}),
      };
      await onSubmit(widget);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="dt-modal-overlay" onClick={onCancel}>
      <div className="dt-modal" onClick={(e) => e.stopPropagation()} style={{ minWidth: 460 }}>
        <div className="dt-modal-title">{isEdit ? "Edit widget" : "New widget"}</div>

        <div className="dt-schema-row">
          <label className="dt-schema-label">Title</label>
          <input
            className="form-input"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Monthly revenue"
            autoFocus
          />
        </div>

        <div className="dt-schema-row">
          <label className="dt-schema-label">Kind</label>
          <select
            className="form-input"
            value={kind}
            onChange={(e) => setKind(e.target.value as WidgetKind)}
            disabled={isEdit}
            title={isEdit ? "Kind can't change once a widget exists — delete and re-create instead." : undefined}
          >
            <option value="chart">{KIND_BLURB.chart}</option>
            <option value="report">{KIND_BLURB.report}</option>
            <option value="kpi">{KIND_BLURB.kpi}</option>
          </select>
        </div>

        <div className="dt-schema-row">
          <label className="dt-schema-label">Prompt</label>
          <textarea
            className="form-input form-textarea"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder={KIND_PLACEHOLDER[kind]}
            rows={4}
          />
        </div>

        <div className="dt-schema-row">
          <label className="dt-schema-label">Refresh</label>
          <select
            className="form-input"
            value={refresh}
            onChange={(e) => setRefresh(e.target.value as WidgetRefresh)}
          >
            <option value="daily">Daily — auto-refresh on first open each day</option>
            <option value="manual">Manual — only when I click refresh</option>
          </select>
        </div>

        <div className="dt-schema-row" style={{ justifyContent: "flex-end", gap: 8 }}>
          <button className="data-dash-action-btn" onClick={onCancel} disabled={saving}>
            Cancel
          </button>
          <button
            className="data-dash-action-btn data-dash-action-btn--primary"
            onClick={() => void handleSubmit()}
            disabled={!canSubmit || saving}
          >
            {saving ? (isEdit ? "Saving…" : "Adding…") : (isEdit ? "Save" : "Add widget")}
          </button>
        </div>
      </div>
    </div>
  );
}
