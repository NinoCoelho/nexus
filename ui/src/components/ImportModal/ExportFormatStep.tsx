import { useTranslation } from "react-i18next";

const SOURCE_LABELS: Record<string, string> = {
  chatgpt: "ChatGPT",
  claude: "Claude",
  gemini: "Gemini",
};

interface ExportFormatStepProps {
  format: string;
  count: number;
  mode: "conversations" | "raw";
  onChange: (mode: "conversations" | "raw") => void;
}

export function ExportFormatStep({ format, count, mode, onChange }: ExportFormatStepProps) {
  const { t } = useTranslation("vault");
  const label = SOURCE_LABELS[format] || format;

  return (
    <div className="import-export-format">
      <div className="import-export-banner">
        <span className="import-export-icon">📦</span>
        <div className="import-export-info">
          <div className="import-export-label">
            {t("vault:import.exportDetected", { source: label, count })}
          </div>
          <div className="import-export-desc">
            {t("vault:import.exportDesc", { source: label })}
          </div>
        </div>
      </div>
      <div className="import-export-options">
        <label className={`import-export-option ${mode === "conversations" ? "active" : ""}`}>
          <input
            type="radio"
            name="export_mode"
            checked={mode === "conversations"}
            onChange={() => onChange("conversations")}
          />
          <div className="import-export-option-content">
            <div className="import-export-option-title">
              {t("vault:import.exportAsConversations")}
            </div>
            <div className="import-export-option-desc">
              {t("vault:import.exportAsConversationsDesc")}
            </div>
          </div>
        </label>
        <label className={`import-export-option ${mode === "raw" ? "active" : ""}`}>
          <input
            type="radio"
            name="export_mode"
            checked={mode === "raw"}
            onChange={() => onChange("raw")}
          />
          <div className="import-export-option-content">
            <div className="import-export-option-title">
              {t("vault:import.exportAsRaw")}
            </div>
            <div className="import-export-option-desc">
              {t("vault:import.exportAsRawDesc")}
            </div>
          </div>
        </label>
      </div>
    </div>
  );
}
