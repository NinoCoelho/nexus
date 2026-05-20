import { useTranslation } from "react-i18next";
import type { ImportCsvInfo } from "../../api/vault";

interface CsvOptionsStepProps {
  csvs: ImportCsvInfo[];
  modes: Record<string, "as_is" | "app">;
  onChange: (modes: Record<string, "as_is" | "app">) => void;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function CsvOptionsStep({ csvs, modes, onChange }: CsvOptionsStepProps) {
  const { t } = useTranslation("vault");

  const handleChange = (path: string, mode: "as_is" | "app") => {
    onChange({ ...modes, [path]: mode });
  };

  return (
    <div className="import-csv-options">
      <div className="import-csv-title">{t("vault:import.csvTitle")}</div>
      <div className="import-csv-desc">{t("vault:import.csvDesc")}</div>
      <div className="import-csv-table">
        <div className="import-csv-header">
          <span className="import-csv-col-name">{t("vault:import.csvFileName")}</span>
          <span className="import-csv-col-cols">{t("vault:import.csvColumns")}</span>
          <span className="import-csv-col-rows">{t("vault:import.csvRows")}</span>
          <span className="import-csv-col-size">{t("vault:import.csvSize")}</span>
          <span className="import-csv-col-action">{t("vault:import.csvAction")}</span>
        </div>
        {csvs.map((csv) => (
          <div key={csv.path} className="import-csv-row">
            <span className="import-csv-col-name" title={csv.path}>
              📊 {csv.name}
            </span>
            <span className="import-csv-col-cols">{csv.column_count}</span>
            <span className="import-csv-col-rows">~{csv.estimated_rows}</span>
            <span className="import-csv-col-size">{formatSize(csv.size)}</span>
            <span className="import-csv-col-action">
              <select
                className="import-csv-select"
                value={modes[csv.path] || "as_is"}
                onChange={(e) => handleChange(csv.path, e.target.value as "as_is" | "app")}
              >
                <option value="as_is">{t("vault:import.csvAsIs")}</option>
                <option value="app">{t("vault:import.csvAsApp")}</option>
              </select>
            </span>
          </div>
        ))}
      </div>
      {csvs.some((c) => modes[c.path] === "app") && (
        <div className="import-csv-app-info">
          {t("vault:import.csvAppInfo")}
        </div>
      )}
    </div>
  );
}
