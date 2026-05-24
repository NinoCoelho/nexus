import { useTranslation } from "react-i18next";

interface ConfirmStepProps {
  fileCount: number;
  totalSize: number;
  processPrompt: string;
  csvApps: string[];
  exportFormat?: { format: string; conversation_count: number } | null;
  exportMode: "conversations" | "raw";
  destDir: string;
  keepOriginals: boolean;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const SOURCE_LABELS: Record<string, string> = {
  chatgpt: "ChatGPT",
  claude: "Claude",
  gemini: "Gemini",
};

export function ConfirmStep({
  fileCount,
  totalSize,
  processPrompt,
  csvApps,
  exportFormat,
  exportMode,
  destDir,
  keepOriginals,
}: ConfirmStepProps) {
  const { t } = useTranslation("vault");

  const items: { label: string; value: string }[] = [
    {
      label: t("vault:import.confirmFiles"),
      value: t("vault:import.confirmFilesValue", {
        count: fileCount,
        size: formatSize(totalSize),
      }),
    },
  ];

  if (processPrompt.trim()) {
    items.push({
      label: t("vault:import.confirmProcess"),
      value: keepOriginals
        ? t("vault:import.confirmProcessKeep")
        : t("vault:import.confirmProcessReplace"),
    });
  }

  if (csvApps.length > 0) {
    items.push({
      label: t("vault:import.confirmCsvApp"),
      value: t("vault:import.confirmCsvAppValue", { count: csvApps.length }),
    });
  }

  if (exportFormat && exportMode === "conversations") {
    items.push({
      label: t("vault:import.confirmConversations"),
      value: t("vault:import.confirmConversationsValue", {
        source: SOURCE_LABELS[exportFormat.format] || exportFormat.format,
        count: exportFormat.conversation_count,
      }),
    });
  }

  items.push({
    label: t("vault:import.confirmDest"),
    value: destDir ? `vault/${destDir}/` : "vault/",
  });

  return (
    <div className="import-confirm">
      <div className="import-confirm-title">{t("vault:import.confirmTitle")}</div>
      <div className="import-confirm-list">
        {items.map((item, i) => (
          <div key={i} className="import-confirm-item">
            <span className="import-confirm-label">{item.label}</span>
            <span className="import-confirm-value">{item.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
