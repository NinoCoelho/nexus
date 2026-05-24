import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import type { ImportLogEntry } from "./index";

interface ImportProgressProps {
  log: ImportLogEntry[];
  stats: { imported: number; processed: number; errors: number } | null;
  onCancel: () => void;
}

export function ImportProgress({ log, stats, onCancel }: ImportProgressProps) {
  const { t } = useTranslation("vault");
  const logEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [log]);

  const done = log.filter((e) => e.status === "done").length;
  const total = log.length || 1;
  const progress = Math.round((done / total) * 100);

  return (
    <div className="import-progress">
      <div className="import-progress-bar-container">
        <div
          className="import-progress-bar"
          style={{ width: `${progress}%` }}
        />
      </div>
      <div className="import-progress-label">
        {stats
          ? t("vault:import.complete")
          : t("vault:import.progress", { done, total })}
      </div>
      <div className="import-log">
        {log.map((entry, i) => (
          <div
            key={i}
            className={`import-log-entry import-log-entry--${entry.status}`}
          >
            <span className="import-log-icon">
              {entry.status === "done" && "✓"}
              {entry.status === "working" && "⚙"}
              {entry.status === "error" && "✗"}
              {entry.status === "pending" && "○"}
            </span>
            <span className="import-log-path">{entry.path}</span>
            <span className="import-log-action">
              {entry.action === "import" && t("vault:import.actionImport")}
              {entry.action === "process" && t("vault:import.actionProcess")}
              {entry.action === "convert" && t("vault:import.actionConvert")}
              {entry.action === "csv_app_queued" && t("vault:import.actionQueued")}
              {entry.action === "create_table" && t("vault:import.actionCreateTable")}
              {entry.action === "migrate_data" && t("vault:import.actionMigrate")}
            </span>
            {entry.error && (
              <span className="import-log-error">{entry.error}</span>
            )}
          </div>
        ))}
        <div ref={logEndRef} />
      </div>
      {!stats && (
        <button className="modal-btn" onClick={onCancel}>
          {t("vault:import.cancelImport")}
        </button>
      )}
    </div>
  );
}
