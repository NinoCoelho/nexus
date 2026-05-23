import { useEffect, useState } from "react";
import { X, Download, SkipForward, ExternalLink, RefreshCw } from "lucide-react";
import {
  type UpdateCheckResult,
  type UpdateStatus,
  getUpdateStatus,
  installUpdate,
  skipVersion,
} from "../../api/update";
import MarkdownView from "../MarkdownView";
import "./UpdateModal.css";

interface Props {
  check: UpdateCheckResult;
  onClose: () => void;
  onSkipped: () => void;
  onInstalled: () => void;
}

type Phase = "confirm" | "downloading" | "ready" | "installing" | "error";

export default function UpdateModal({ check, onClose, onSkipped, onInstalled }: Props) {
  const [phase, setPhase] = useState<Phase>(check.update_available ? "confirm" : "confirm");
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState("");
  const [status, setStatus] = useState<UpdateStatus | null>(null);

  useEffect(() => {
    getUpdateStatus().then(setStatus).catch(() => {});
  }, []);

  useEffect(() => {
    if (status?.state === "ready") {
      setPhase("ready");
    }
  }, [status]);

  useEffect(() => {
    if (phase !== "downloading") return;
    let cancelled = false;
    const poll = async () => {
      while (!cancelled) {
        try {
          const s = await getUpdateStatus();
          if (cancelled) return;
          if (s.state === "downloading") {
            setProgress(s.progress ?? 0);
          } else if (s.state === "ready") {
            setPhase("ready");
            return;
          } else if (s.state === "error") {
            setPhase("error");
            setError(s.error ?? "Download failed");
            return;
          }
        } catch {
          // ignore
        }
        await new Promise((r) => setTimeout(r, 1000));
      }
    };
    poll();
    return () => { cancelled = true; };
  }, [phase]);

  const handleDownload = async () => {
    setPhase("downloading");
    try {
      const { BASE } = await import("../../api/base");
      const res = await fetch(`${BASE}/update/download`, { method: "POST" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      // SSE stream — read to completion
      const reader = res.body?.getReader();
      if (reader) {
        const decoder = new TextDecoder();
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          const text = decoder.decode(value, { stream: true });
          const match = text.match(/"progress":\s*([\d.]+)/);
          if (match) setProgress(parseFloat(match[1]));
        }
      }
      const s = await getUpdateStatus();
      if (s.state === "ready") setPhase("ready");
    } catch (e: any) {
      setPhase("error");
      setError(e.message ?? "Download failed");
    }
  };

  const handleInstall = async () => {
    setPhase("installing");
    try {
      await installUpdate();
      onInstalled();
    } catch (e: any) {
      setPhase("error");
      setError(e.message ?? "Install failed");
    }
  };

  const handleSkip = async () => {
    await skipVersion(check.latest);
    onSkipped();
  };

  return (
    <div className="update-backdrop" onClick={onClose}>
      <div className="update-dialog" onClick={(e) => e.stopPropagation()}>
        <div className="update-header">
          <h2 className="update-title">
            <RefreshCw size={20} />
            {phase === "confirm" && "Update Available"}
            {phase === "downloading" && "Downloading Update…"}
            {phase === "ready" && "Ready to Install"}
            {phase === "installing" && "Installing Update…"}
            {phase === "error" && "Update Error"}
          </h2>
          {phase === "confirm" && (
            <button className="update-close" onClick={onClose}>
              <X size={18} />
            </button>
          )}
        </div>

        <div className="update-versions">
          <span className="update-version-label">
            Current: <strong>{check.current}</strong>
          </span>
          <span className="update-version-arrow">→</span>
          <span className="update-version-label update-version-latest">
            Latest: <strong>{check.latest}</strong>
          </span>
        </div>

        {check.body && (
          <div className="update-notes">
            <MarkdownView>{check.body}</MarkdownView>
          </div>
        )}

        {phase === "downloading" && (
          <div className="update-progress">
            <div className="update-progress-bar">
              <div
                className="update-progress-fill"
                style={{ width: `${Math.round(progress * 100)}%` }}
              />
            </div>
            <span className="update-progress-text">{Math.round(progress * 100)}%</span>
          </div>
        )}

        {phase === "error" && (
          <div className="update-error">
            <p>{error}</p>
          </div>
        )}

        {phase === "installing" && (
          <div className="update-installing">
            <div className="update-spinner" />
            <p>The app will quit and install the update. Please wait…</p>
          </div>
        )}

        <div className="update-actions">
          {phase === "confirm" && (
            <>
              <button className="update-btn update-btn--secondary" onClick={handleSkip}>
                <SkipForward size={16} />
                Skip This Version
              </button>
              {check.html_url && (
                <a
                  href={check.html_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="update-btn update-btn--secondary"
                >
                  <ExternalLink size={16} />
                  Release Page
                </a>
              )}
              <button className="update-btn update-btn--primary" onClick={handleDownload}>
                <Download size={16} />
                Download Update
              </button>
            </>
          )}
          {phase === "downloading" && (
            <button className="update-btn update-btn--secondary" disabled>
              Downloading…
            </button>
          )}
          {phase === "ready" && (
            <button className="update-btn update-btn--primary" onClick={handleInstall}>
              <Download size={16} />
              Restart & Install
            </button>
          )}
          {phase === "error" && (
            <button className="update-btn update-btn--primary" onClick={handleDownload}>
              <Download size={16} />
              Retry Download
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
