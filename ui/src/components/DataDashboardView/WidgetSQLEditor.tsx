import { useCallback, useState } from "react";
import {
  addWidget,
  previewWidget,
  type DashboardWidget,
  type WidgetQueryResult,
  type VizType,
} from "../../api/dashboard";
import { useToast } from "../../toast/ToastProvider";
import "../Modal.css";
import { getVizComponent } from "./viz";
import type { VizConfig } from "./viz/types";

interface Props {
  folder: string;
  widget: DashboardWidget;
  onClose: () => void;
  onSaved: (widget: DashboardWidget) => void;
}

export default function WidgetSQLEditor({ folder, widget, onClose, onSaved }: Props) {
  const toast = useToast();
  const [sql, setSql] = useState(widget.query ?? "");
  const [queryTables] = useState(widget.query_tables ?? []);
  const [previewResult, setPreviewResult] = useState<WidgetQueryResult | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);

  const handleTest = useCallback(async () => {
    if (!sql.trim()) return;
    setTesting(true);
    setPreviewError(null);
    setPreviewResult(null);
    try {
      const result = await previewWidget(folder, {
        query: sql,
        viz_type: widget.viz_type as VizType,
        viz_config: widget.viz_config as VizConfig | undefined,
        query_tables: queryTables.length > 0 ? queryTables : undefined,
      });
      if (result.error) {
        setPreviewError(result.error);
      } else {
        setPreviewResult(result);
      }
    } catch (e) {
      setPreviewError((e as Error).message);
    } finally {
      setTesting(false);
    }
  }, [sql, folder, widget.viz_type, widget.viz_config, queryTables]);

  const handleSave = useCallback(async () => {
    if (!sql.trim()) return;
    setSaving(true);
    try {
      const updated: DashboardWidget = {
        ...widget,
        query: sql,
        user_defined: true,
      };
      await addWidget(folder, updated);
      toast.success(`Saved "${widget.title}"`);
      onSaved(updated);
    } catch (e) {
      toast.error(`Couldn't save "${widget.title}"`, { detail: (e as Error).message });
    } finally {
      setSaving(false);
    }
  }, [sql, widget, folder, onSaved, toast]);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal-dialog widget-sql-editor"
        onClick={(e) => e.stopPropagation()}
        style={{ maxWidth: 640, width: "90vw" }}
      >
        <div className="modal-title">{"Edit SQL \u2014 " + widget.title}</div>

        <textarea
          className="widget-sql-editor-textarea"
          value={sql}
          onChange={(e) => setSql(e.target.value)}
          spellCheck={false}
          autoFocus
          rows={8}
          placeholder="SELECT ..."
        />

        {previewError && (
          <div className="widget-sql-editor-error">{previewError}</div>
        )}

        {previewResult && previewResult.rows.length > 0 && (
          <div className="widget-sql-editor-preview">
            <div className="widget-sql-editor-preview-header">
              {previewResult.row_count} row{previewResult.row_count !== 1 ? "s" : ""}
              {previewResult.truncated ? " (truncated)" : ""}
            </div>
            <WidgetPreviewViz
              vizType={widget.viz_type}
              result={previewResult}
              config={(widget.viz_config as VizConfig) ?? {}}
            />
          </div>
        )}

        {previewResult && previewResult.rows.length === 0 && (
          <div className="widget-sql-editor-preview">
            <div className="widget-sql-editor-preview-header">Query returned 0 rows.</div>
          </div>
        )}

        <div className="modal-actions">
          <button className="modal-btn" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button
            className="modal-btn"
            onClick={void handleTest}
            disabled={testing || saving || !sql.trim()}
          >
            {testing ? "Testing\u2026" : "Test"}
          </button>
          <button
            className="modal-btn modal-btn--primary"
            onClick={void handleSave}
            disabled={saving || !sql.trim()}
          >
            {saving ? "Saving\u2026" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}

function WidgetPreviewViz({
  vizType,
  result,
  config,
}: {
  vizType: string;
  result: WidgetQueryResult;
  config: VizConfig;
}) {
  try {
    const Component = getVizComponent(vizType as never);
    return <Component columns={result.columns} rows={result.rows} config={config} />;
  } catch {
    return (
      <div className="widget-sql-editor-preview-header">Preview not available for this viz type.</div>
    );
  }
}
