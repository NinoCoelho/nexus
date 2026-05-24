import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type {
  ImportOptions,
  ImportStats,
  ImportSseEvent,
  ImportTreeNode,
  CsvProposal,
} from "../../api/vault";
import {
  cancelZipImport,
  streamZipImport,
  streamBatchImport,
  analyzeCsv,
  streamCsvMigrate,
} from "../../api/vault";
import { useToast } from "../../toast/ToastProvider";
import { FileSelectStep } from "./FileSelectStep";
import { ExportFormatStep } from "./ExportFormatStep";
import { CsvOptionsStep } from "./CsvOptionsStep";
import { ProcessStep } from "./ProcessStep";
import { ConfirmStep } from "./ConfirmStep";
import { ImportProgress } from "./ImportProgress";
import { CsvReviewPanel } from "./CsvReviewPanel";
import "./styles.css";

export type ImportSource =
  | { type: "zip"; importId: string }
  | { type: "drop"; files: Map<string, File> };

export type WizardStep =
  | "select"
  | "export_format"
  | "csv_options"
  | "process"
  | "confirm"
  | "importing"
  | "csv_review"
  | "csv_migrating"
  | "done";

export interface ImportLogEntry {
  path: string;
  action: string;
  status: "pending" | "working" | "done" | "error";
  error?: string;
  size?: number;
}

export interface ImportModalProps {
  source: ImportSource;
  initialTree: ImportTreeNode[];
  stats: ImportStats;
  exportFormat?: { format: string; conversation_count: number } | null;
  onClose: () => void;
  onComplete: () => void;
}

export default function ImportModal({
  source,
  initialTree,
  stats,
  exportFormat,
  onClose,
  onComplete,
}: ImportModalProps) {
  const { t } = useTranslation("vault");
  const toast = useToast();

  const [step, setStep] = useState<WizardStep>("select");
  const [checkedPaths, setCheckedPaths] = useState<Set<string>>(() => {
    const all = new Set<string>();
    const collect = (nodes: ImportTreeNode[]) => {
      for (const n of nodes) {
        all.add(n.path);
        if (n.children) collect(n.children);
      }
    };
    collect(initialTree);
    return all;
  });
  const [exportMode, setExportMode] = useState<"conversations" | "raw">("conversations");
  const [csvModes, setCsvModes] = useState<Record<string, "as_is" | "app">>(() => {
    const modes: Record<string, "as_is" | "app"> = {};
    for (const c of stats.csvs) modes[c.path] = "as_is";
    return modes;
  });
  const [processPrompt, setProcessPrompt] = useState("");
  const [keepOriginals, setKeepOriginals] = useState(true);
  const [destDir, setDestDir] = useState(() => _suggestDestDir(source, initialTree));
  const [log, setLog] = useState<ImportLogEntry[]>([]);
  const [importStats, setImportStats] = useState<{
    imported: number;
    processed: number;
    errors: number;
  } | null>(null);
  const [csvApps, setCsvApps] = useState<string[]>([]);
  const [csvProposal, setCsvProposal] = useState<CsvProposal | null>(null);
  const [csvAnalyzing, setCsvAnalyzing] = useState(false);
  const [batchId, setBatchId] = useState<string | undefined>();
  const abortRef = useRef<AbortController | null>(null);

  const hasExport = !!exportFormat;
  const hasCsvs = stats.csvs.length > 0;
  const selectedFiles = useMemo(() => _countSelected(initialTree, checkedPaths), [initialTree, checkedPaths]);
  const selectedCsvs = useMemo(
    () => stats.csvs.filter((c) => checkedPaths.has(c.path)),
    [stats.csvs, checkedPaths],
  );
  const csvAppPaths = useMemo(
    () => selectedCsvs.filter((c) => csvModes[c.path] === "app").map((c) => c.path),
    [selectedCsvs, csvModes],
  );
  const fileCount = selectedFiles.files;
  const totalSize = selectedFiles.size;

  const nextStep = useCallback(() => {
    if (step === "select") {
      if (hasExport) {
        setStep("export_format");
      } else if (hasCsvs && selectedCsvs.length > 0) {
        setStep("csv_options");
      } else {
        setStep("process");
      }
    } else if (step === "export_format") {
      if (hasCsvs && selectedCsvs.length > 0) {
        setStep("csv_options");
      } else {
        setStep("process");
      }
    } else if (step === "csv_options") {
      setStep("process");
    } else if (step === "process") {
      setStep("confirm");
    }
  }, [step, hasExport, hasCsvs, selectedCsvs.length]);

  const prevStep = useCallback(() => {
    if (step === "export_format") setStep("select");
    else if (step === "csv_options") {
      if (hasExport) setStep("export_format");
      else setStep("select");
    } else if (step === "process") {
      if (hasCsvs && selectedCsvs.length > 0) setStep("csv_options");
      else if (hasExport) setStep("export_format");
      else setStep("select");
    } else if (step === "confirm") setStep("process");
  }, [step, hasExport, hasCsvs, selectedCsvs.length]);

  const buildOptions = useCallback((): ImportOptions => {
    const opts: ImportOptions = {
      selected_paths: Array.from(checkedPaths),
      dest_dir: destDir,
      csv_options: csvModes,
    };
    if (processPrompt.trim()) {
      opts.process_options = { prompt: processPrompt.trim(), keep_originals: keepOriginals };
    }
    if (exportFormat && exportMode === "conversations") {
      opts.export_options = {
        format: exportFormat.format,
        import_as: "conversations",
      };
    }
    return opts;
  }, [checkedPaths, destDir, csvModes, processPrompt, keepOriginals, exportFormat, exportMode]);

  const handleImportEvent = useCallback((event: ImportSseEvent) => {
    if (event.event === "file_start") {
      setLog((prev) => [
        ...prev,
        { path: event.data.path, action: event.data.action, status: "working" },
      ]);
    } else if (event.event === "file_done") {
      setLog((prev) =>
        prev.map((e, i) =>
          i === prev.length - 1 && e.path === event.data.path
            ? { ...e, status: "done" as const, size: event.data.size }
            : e,
        ),
      );
    } else if (event.event === "file_error") {
      setLog((prev) =>
        prev.map((e, i) =>
          i === prev.length - 1 && e.path === event.data.path
            ? { ...e, status: "error" as const, error: event.data.error }
            : e,
        ),
      );
    } else if (event.event === "done") {
      setImportStats(event.data.stats);
      setCsvApps(event.data.csv_apps || []);
      if (event.data.batch_id) setBatchId(event.data.batch_id);
    }
  }, []);

  const startImport = useCallback(async () => {
    setStep("importing");
    setLog([]);
    setImportStats(null);
    const abort = new AbortController();
    abortRef.current = abort;
    const opts = buildOptions();

    try {
      if (source.type === "zip") {
        await streamZipImport(source.importId, opts, handleImportEvent, abort.signal);
      } else {
        const bid = await streamBatchImport(source.files, opts, handleImportEvent, abort.signal);
        if (bid) setBatchId(bid);
      }
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        toast.error(t("vault:import.importFailed"), {
          detail: (err as Error).message,
        });
      }
    }
  }, [source, buildOptions, handleImportEvent, toast, t]);

  useEffect(() => {
    if (step === "importing" && importStats) {
      if (csvAppPaths.length > 0 && csvApps.length > 0) {
        setStep("csv_review");
        _analyzeNextCsv(csvApps, 0, source, batchId, setCsvAnalyzing, setCsvProposal, toast, t);
      } else {
        setStep("done");
      }
    }
  }, [step, importStats, csvAppPaths.length, csvApps.length, source, batchId, toast, t]);

  const handleCsvApprove = useCallback(async () => {
    if (!csvProposal || csvApps.length === 0) return;
    setStep("csv_migrating");
    const csvPath = csvApps[0];
    const abort = new AbortController();
    abortRef.current = abort;

    try {
      await streamCsvMigrate(
        {
          csv_path: csvPath,
          import_id: source.type === "zip" ? source.importId : undefined,
          batch_id: batchId,
          dest_dir: destDir,
          approved_plan: csvProposal,
        },
        handleImportEvent,
        abort.signal,
      );
      setCsvApps((prev) => prev.slice(1));
      setCsvProposal(null);
      if (csvApps.length <= 1) {
        setStep("done");
      } else {
        setStep("csv_review");
        await _analyzeNextCsv(
          csvApps.slice(1),
          0,
          source,
          batchId,
          setCsvAnalyzing,
          setCsvProposal,
          toast,
          t,
        );
      }
    } catch (err) {
      toast.error(t("vault:import.csvMigrateFailed"), {
        detail: (err as Error).message,
      });
      setStep("done");
    }
  }, [csvProposal, csvApps, source, batchId, destDir, handleImportEvent, toast, t]);

  const handleCancel = useCallback(() => {
    abortRef.current?.abort();
    if (source.type === "zip" && (step === "importing")) {
      void cancelZipImport(source.importId);
    }
    onClose();
  }, [source, step, onClose]);

  const handleDone = useCallback(() => {
    onComplete();
    onClose();
  }, [onComplete, onClose]);

  const escHandler = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        if (step === "importing" || step === "csv_review" || step === "csv_migrating") return;
        handleCancel();
      }
    },
    [step, handleCancel],
  );
  useEffect(() => {
    window.addEventListener("keydown", escHandler);
    return () => window.removeEventListener("keydown", escHandler);
  }, [escHandler]);

  const renderStep = () => {
    switch (step) {
      case "select":
        return (
          <FileSelectStep
            tree={initialTree}
            checkedPaths={checkedPaths}
            onCheck={setCheckedPaths}
            fileCount={fileCount}
            totalSize={totalSize}
          />
        );
      case "export_format":
        return (
          <ExportFormatStep
            format={exportFormat!.format}
            count={exportFormat!.conversation_count}
            mode={exportMode}
            onChange={setExportMode}
          />
        );
      case "csv_options":
        return (
          <CsvOptionsStep csvs={selectedCsvs} modes={csvModes} onChange={setCsvModes} />
        );
      case "process":
        return (
          <ProcessStep
            prompt={processPrompt}
            onPromptChange={setProcessPrompt}
            keepOriginals={keepOriginals}
            onKeepOriginalsChange={setKeepOriginals}
            destDir={destDir}
            onDestDirChange={setDestDir}
            fileCount={fileCount}
          />
        );
      case "confirm":
        return (
          <ConfirmStep
            fileCount={fileCount}
            totalSize={totalSize}
            processPrompt={processPrompt}
            csvApps={csvAppPaths}
            exportFormat={exportFormat}
            exportMode={exportMode}
            destDir={destDir}
            keepOriginals={keepOriginals}
          />
        );
      case "importing":
        return (
          <ImportProgress
            log={log}
            stats={importStats}
            onCancel={handleCancel}
          />
        );
      case "csv_review":
        return (
          <CsvReviewPanel
            proposal={csvProposal}
            analyzing={csvAnalyzing}
            onApprove={handleCsvApprove}
            onSkip={() => setStep("done")}
          />
        );
      case "csv_migrating":
        return (
          <ImportProgress
            log={log}
            stats={importStats}
            onCancel={handleCancel}
          />
        );
      case "done":
        return (
          <div className="import-done">
            <div className="import-done-icon">&#10003;</div>
            <div className="import-done-title">{t("vault:import.doneTitle")}</div>
            {importStats && (
              <div className="import-done-stats">
                <span>{t("vault:import.importedCount", { count: importStats.imported })}</span>
                {importStats.processed > 0 && (
                  <span>{t("vault:import.processedCount", { count: importStats.processed })}</span>
                )}
                {importStats.errors > 0 && (
                  <span className="import-done-errors">
                    {t("vault:import.errorCount", { count: importStats.errors })}
                  </span>
                )}
              </div>
            )}
            <button className="modal-btn modal-btn--primary" onClick={handleDone}>
              {t("vault:import.doneButton")}
            </button>
          </div>
        );
    }
  };

  const showBack = step !== "importing" && step !== "csv_review" && step !== "csv_migrating" && step !== "done";
  const showNext = step === "select" || step === "export_format" || step === "csv_options" || step === "process";
  const showImport = step === "confirm";

  return (
    <div className="modal-backdrop" onClick={handleCancel}>
      <div className="import-modal-dialog" onClick={(e) => e.stopPropagation()}>
        <div className="import-modal-title">{t("vault:import.title")}</div>
        <div className="import-modal-body">{renderStep()}</div>
        <div className="import-modal-footer">
          {showBack && (
            <button className="modal-btn" onClick={prevStep}>
              {t("vault:import.back")}
            </button>
          )}
          <div className="import-modal-footer-spacer" />
          {showNext && (
            <button
              className="modal-btn modal-btn--primary"
              onClick={nextStep}
              disabled={fileCount === 0}
            >
              {t("vault:import.next")}
            </button>
          )}
          {showImport && (
            <button className="modal-btn modal-btn--primary" onClick={startImport}>
              {t("vault:import.startImport")}
            </button>
          )}
          {step !== "importing" && step !== "csv_migrating" && step !== "done" && (
            <button className="modal-btn" onClick={handleCancel}>
              {t("vault:import.cancel")}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function _suggestDestDir(source: ImportSource, tree: ImportTreeNode[]): string {
  if (tree.length === 1 && tree[0].type === "dir") {
    return tree[0].name.replace(/[^\w.\-]/g, "_");
  }
  if (source.type === "zip") {
    return "import";
  }
  return "";
}

function _countSelected(
  tree: ImportTreeNode[],
  checked: Set<string>,
): { files: number; size: number } {
  let files = 0;
  let size = 0;
  const walk = (nodes: ImportTreeNode[]) => {
    for (const n of nodes) {
      if (n.type === "file" && checked.has(n.path)) {
        files++;
        size += n.size || 0;
      }
      if (n.children) walk(n.children);
    }
  };
  walk(tree);
  return { files, size };
}

async function _analyzeNextCsv(
  csvApps: string[],
  index: number,
  source: ImportSource,
  batchId: string | undefined,
  setAnalyzing: (v: boolean) => void,
  setProposal: (p: CsvProposal | null) => void,
  toast: { error: (msg: string, opts?: { detail?: string }) => void },
  t: (key: string) => string,
) {
  if (index >= csvApps.length) return;
  setAnalyzing(true);
  setProposal(null);
  try {
    const result = await analyzeCsv({
      csv_path: csvApps[index],
      import_id: source.type === "zip" ? source.importId : undefined,
      batch_id: batchId,
    });
    setProposal(result.proposal);
  } catch (err) {
    toast.error(t("vault:import.csvAnalyzeFailed"), {
      detail: (err as Error).message,
    });
    setProposal(null);
  } finally {
    setAnalyzing(false);
  }
}
