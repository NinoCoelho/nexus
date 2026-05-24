import { useTranslation } from "react-i18next";

interface ProcessStepProps {
  prompt: string;
  onPromptChange: (v: string) => void;
  keepOriginals: boolean;
  onKeepOriginalsChange: (v: boolean) => void;
  destDir: string;
  onDestDirChange: (v: string) => void;
  fileCount: number;
}

export function ProcessStep({
  prompt,
  onPromptChange,
  keepOriginals,
  onKeepOriginalsChange,
  destDir,
  onDestDirChange,
  fileCount,
}: ProcessStepProps) {
  const { t } = useTranslation("vault");

  return (
    <div className="import-process">
      <div className="import-process-label">{t("vault:import.processLabel")}</div>
      <textarea
        className="import-process-textarea"
        value={prompt}
        onChange={(e) => onPromptChange(e.target.value)}
        placeholder={t("vault:import.processPlaceholder")}
        rows={3}
      />
      <div className="import-process-skip">{t("vault:import.processSkip")}</div>

      {prompt.trim() && (
        <div className="import-process-warnings">
          <div className="import-warning-banner">
            <span className="import-warning-icon">⚠️</span>
            <div>
              <div>{t("vault:import.processWarningUsage", { count: fileCount })}</div>
              <div className="import-warning-detail">
                {t("vault:import.processWarningCloud")}
              </div>
              <div className="import-warning-detail">
                {t("vault:import.processWarningReasoning")}
              </div>
            </div>
          </div>
          <label className="import-checkbox-label">
            <input
              type="checkbox"
              checked={keepOriginals}
              onChange={(e) => onKeepOriginalsChange(e.target.checked)}
            />
            {t("vault:import.keepOriginals")}
          </label>
        </div>
      )}

      <div className="import-dest-dir">
        <label className="import-dest-dir-label">{t("vault:import.destDirLabel")}</label>
        <input
          type="text"
          className="import-dest-dir-input"
          value={destDir}
          onChange={(e) => onDestDirChange(e.target.value)}
          placeholder={t("vault:import.destDirPlaceholder")}
        />
      </div>
    </div>
  );
}
