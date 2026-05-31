import { useEffect, useRef, useState, useCallback } from "react";
import {
  testTriggerListenUrl,
  cancelTestListener,
  testTrigger,
  listFsWatchFiles,
  pickFsTestFile,
  getWebhookUrl,
  brokerDequeue,
} from "../../api/workflows";
import type { TriggerType, TriggerConfig } from "../../types/workflow";
import type { FsWatchFile } from "../../api/workflows";

type ModalState =
  | "connecting"
  | "listening"
  | "captured"
  | "timeout"
  | "error";

const TRIGGER_TYPE_LABELS: Record<TriggerType, string> = {
  webhook: "Webhook",
  fs_watch: "File Watch",
  schedule: "Schedule",
  manual: "Manual",
  event: "Event",
  rss: "RSS Feed",
};

interface Props {
  wfPath: string;
  triggerId: string;
  triggerType: TriggerType;
  triggerConfig: TriggerConfig;
  onClose: () => void;
  onRunWithPayload: (payload: Record<string, unknown>) => void;
}

function isBrokerWebhook(triggerType: TriggerType, config: TriggerConfig): boolean {
  return triggerType === "webhook" && !!config.broker_id;
}

async function* readSSEResponse(
  response: Response,
): AsyncGenerator<{ event: string; data: unknown }> {
  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    let currentEvent = "";
    let currentData = "";
    for (const line of lines) {
      if (line.startsWith("event: ")) currentEvent = line.slice(7);
      else if (line.startsWith("data: ")) currentData = line.slice(6);
      else if (line === "" && currentEvent) {
        yield { event: currentEvent, data: JSON.parse(currentData) };
        currentEvent = "";
        currentData = "";
      }
    }
  }
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function TriggerTestModal({
  wfPath,
  triggerId,
  triggerType,
  triggerConfig,
  onClose,
  onRunWithPayload,
}: Props) {
  const brokerMode = isBrokerWebhook(triggerType, triggerConfig);
  const [state, setState] = useState<ModalState>(
    triggerType === "schedule" || triggerType === "rss" || brokerMode
      ? "captured"
      : "connecting",
  );
  const [capturedPayload, setCapturedPayload] = useState<
    Record<string, unknown> | null
  >(null);
  const [manualPayload, setManualPayload] = useState("{}");
  const [manualError, setManualError] = useState("");
  const [brokerTab, setBrokerTab] = useState<"consume" | "manual">("consume");
  const [dequeueLoading, setDequeueLoading] = useState(false);
  const [dequeueResult, setDequeueResult] = useState<{
    payload: Record<string, unknown> | null;
    message?: string;
  } | null>(null);
  const [listenInfo, setListenInfo] = useState<{
    url?: string;
    path?: string;
    pattern?: string;
  }>({});
  const [error, setError] = useState("");
  const [seconds, setSeconds] = useState(60);
  const [fsFiles, setFsFiles] = useState<FsWatchFile[]>([]);
  const [fsLoading, setFsLoading] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const testIdRef = useRef<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const cleanup = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    if (testIdRef.current) {
      cancelTestListener(wfPath, testIdRef.current).catch(() => {});
      testIdRef.current = null;
    }
  }, [wfPath]);

  useEffect(() => {
    if (!brokerMode) return;
    let cancelled = false;
    (async () => {
      try {
        const info = await getWebhookUrl(wfPath);
        if (cancelled) return;
        const hook = info.webhooks.find((w) => w.trigger_id === triggerId);
        if (hook?.url) {
          setListenInfo({ url: hook.url });
        }
      } catch {}
    })();
    return () => { cancelled = true; };
  }, [brokerMode, wfPath, triggerId]);

  useEffect(() => {
    if (triggerType === "schedule" || triggerType === "rss" || brokerMode) return;

    const ac = new AbortController();
    abortRef.current = ac;

    (async () => {
      try {
        const url = testTriggerListenUrl(wfPath);
        const res = await fetch(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ trigger_id: triggerId }),
          signal: ac.signal,
        });

        if (!res.ok) {
          setError(await res.text());
          setState("error");
          return;
        }

        for await (const evt of readSSEResponse(res)) {
          if (ac.signal.aborted) break;

          const data = evt.data as Record<string, unknown>;

          if (evt.event === "test.listening") {
            setState("listening");
            testIdRef.current = (data.test_id as string) || null;
            setListenInfo({
              url: data.url as string | undefined,
              path: data.path as string | undefined,
              pattern: data.pattern as string | undefined,
            });
            timerRef.current = setInterval(() => {
              setSeconds((s) => {
                if (s <= 1) {
                  if (timerRef.current) clearInterval(timerRef.current);
                  setState("timeout");
                  return 0;
                }
                return s - 1;
              });
            }, 1000);
          } else if (evt.event === "test.captured") {
            setCapturedPayload(
              (data.payload as Record<string, unknown>) || {},
            );
            setState("captured");
            if (timerRef.current) clearInterval(timerRef.current);
          } else if (evt.event === "test.timeout") {
            setState("timeout");
            if (timerRef.current) clearInterval(timerRef.current);
          } else if (evt.event === "test.error") {
            setError((data.error as string) || "Unknown error");
            setState("error");
            if (timerRef.current) clearInterval(timerRef.current);
          }
        }
      } catch (err: unknown) {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setError(err instanceof Error ? err.message : String(err));
        setState("error");
      }
    })();

    return cleanup;
  }, [triggerType, wfPath, triggerId, cleanup]);

  useEffect(() => {
    if (triggerType !== "fs_watch" || state !== "listening") return;
    let cancelled = false;
    setFsLoading(true);
    listFsWatchFiles(wfPath, triggerId)
      .then((result) => {
        if (!cancelled) setFsFiles(result.files);
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setFsLoading(false);
      });
    return () => { cancelled = true; };
  }, [triggerType, wfPath, triggerId, state]);

  const handleRunSchedule = async () => {
    try {
      const result = await testTrigger(wfPath, triggerId);
      setCapturedPayload(result.trigger_payload);
      onRunWithPayload(result.trigger_payload);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
      setState("error");
    }
  };

  const handleBrokerRun = () => {
    try {
      const parsed = JSON.parse(manualPayload);
      onRunWithPayload(parsed);
    } catch {
      setManualError("Invalid JSON payload");
    }
  };

  const handleDequeue = async () => {
    setDequeueLoading(true);
    setDequeueResult(null);
    try {
      const result = await brokerDequeue(wfPath, triggerId);
      setDequeueResult(result);
      if (result.payload) {
        setCapturedPayload(result.payload);
        setState("captured");
      }
    } catch (err: unknown) {
      setDequeueResult({ payload: null, message: err instanceof Error ? err.message : String(err) });
    }
    setDequeueLoading(false);
  };

  const handlePickFile = useCallback(async (file: FsWatchFile) => {
    try {
      const payload = await pickFsTestFile(wfPath, triggerId, file.path, testIdRef.current || undefined);
      setCapturedPayload(payload);
      setState("captured");
      if (timerRef.current) clearInterval(timerRef.current);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
      setState("error");
    }
  }, [wfPath, triggerId]);

  const handleCancel = () => {
    cleanup();
    onClose();
  };

  const statusIcon: Record<ModalState, string> = {
    connecting: "\u25CE",
    listening: "\uD83D\uDD34",
    captured: "\u2705",
    timeout: "\u23F0",
    error: "\u274C",
  };

  const statusText: Record<ModalState, string> = {
    connecting: "Connecting\u2026",
    listening: "Listening for test event\u2026",
    captured: "Event captured!",
    timeout: "Timed out \u2014 no event received within 60s",
    error: `Error: ${error}`,
  };

  let triggerInfo: React.ReactNode = null;
  if (state === "listening" || state === "captured") {
    if (triggerType === "webhook" && listenInfo.url) {
      triggerInfo = (
        <div className="wf-test-trigger-info">
          <label>Webhook URL</label>
          <code>{listenInfo.url}</code>
        </div>
      );
    } else if (triggerType === "fs_watch" && listenInfo.path) {
      triggerInfo = (
        <div className="wf-test-trigger-info">
          <label>Watching path</label>
          <code>{listenInfo.path}</code>
        </div>
      );
    } else if (triggerType === "event" && listenInfo.pattern) {
      triggerInfo = (
        <div className="wf-test-trigger-info">
          <label>Event pattern</label>
          <code>{listenInfo.pattern}</code>
        </div>
      );
    }
  }

  let fsFilePicker: React.ReactNode = null;
  if (triggerType === "fs_watch" && state === "listening") {
    if (fsLoading) {
      fsFilePicker = (
        <div className="wf-test-file-picker">
          <label>Or select a file to test with</label>
          <div style={{ color: "var(--text-muted)", fontSize: 12, padding: "8px 0" }}>
            Loading files...
          </div>
        </div>
      );
    } else if (fsFiles.length > 0) {
      fsFilePicker = (
        <div className="wf-test-file-picker">
          <label>Or select a file to test with</label>
          <div className="wf-test-file-list">
            {fsFiles.map((f) => (
              <button
                key={f.path}
                className="wf-test-file-item"
                onClick={() => handlePickFile(f)}
                title={f.path}
              >
                <span className="wf-test-file-name">{f.name}</span>
                <span className="wf-test-file-size">{formatFileSize(f.size)}</span>
              </button>
            ))}
          </div>
        </div>
      );
    } else if (listenInfo.path) {
      fsFilePicker = (
        <div className="wf-test-file-picker">
          <label>No matching files found</label>
          <div style={{ color: "var(--text-muted)", fontSize: 12 }}>
            Add or update a file in <code>{listenInfo.path}</code> to trigger,
            or wait for a live event above.
          </div>
        </div>
      );
    }
  }

  if (triggerType === "schedule" || triggerType === "rss") {
    const isSchedule = triggerType === "schedule";
    return (
      <div className="wf-test-overlay">
        <div className="wf-test-modal">
          <div className="wf-test-header">
            <h3>Test Trigger: {TRIGGER_TYPE_LABELS[triggerType]}</h3>
            <button className="wf-test-close" onClick={onClose}>
              {"\u2715"}
            </button>
          </div>
          <div className="wf-test-body">
            <div className="wf-test-status">
              <span className="wf-test-status-icon">{"\u2705"}</span>
              <span className="wf-test-status-text">
                Ready to run with generated{" "}
                {isSchedule ? "schedule" : "RSS feed"} payload
              </span>
            </div>
          </div>
          <div className="wf-test-footer">
            <button className="wf-test-btn" onClick={onClose}>
              Cancel
            </button>
            <button
              className="wf-test-btn primary"
              onClick={handleRunSchedule}
            >
              Run Now
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (brokerMode) {
    return (
      <div className="wf-test-overlay">
        <div className="wf-test-modal">
          <div className="wf-test-header">
            <h3>Test Trigger: {TRIGGER_TYPE_LABELS[triggerType]}</h3>
            <button className="wf-test-close" onClick={onClose}>
              {"\u2715"}
            </button>
          </div>
          <div className="wf-test-body">
            {listenInfo.url && (
              <div className="wf-test-trigger-info">
                <label>Broker Webhook URL</label>
                <code>{listenInfo.url}</code>
              </div>
            )}

            <div className="wf-broker-tabs">
              <button
                className={`wf-broker-tab${brokerTab === "consume" ? " active" : ""}`}
                onClick={() => setBrokerTab("consume")}
              >
                Consume from Queue
              </button>
              <button
                className={`wf-broker-tab${brokerTab === "manual" ? " active" : ""}`}
                onClick={() => setBrokerTab("manual")}
              >
                Enter Payload
              </button>
            </div>

            {brokerTab === "consume" && (
              <div className="wf-broker-panel">
                <div style={{ color: "var(--text-muted)", fontSize: 12, marginBottom: 10 }}>
                  Dequeue the next pending message from the broker and use it as the trigger payload.
                  The message will be consumed (removed from the queue).
                </div>
                <button
                  className="wf-test-btn primary"
                  onClick={handleDequeue}
                  disabled={dequeueLoading}
                  style={{ marginBottom: 10 }}
                >
                  {dequeueLoading ? "Dequeueing…" : "Dequeue Next Message"}
                </button>
                {dequeueResult && !dequeueResult.payload && (
                  <div style={{ color: "var(--text-muted)", fontSize: 12 }}>
                    {dequeueResult.message || "No messages in queue."}
                  </div>
                )}
                {state === "captured" && capturedPayload && (
                  <>
                    <div className="wf-test-payload-label">Dequeued Payload</div>
                    <pre className="wf-test-payload">
                      {JSON.stringify(capturedPayload, null, 2)}
                    </pre>
                  </>
                )}
              </div>
            )}

            {brokerTab === "manual" && (
              <div className="wf-broker-panel">
                <div style={{ color: "var(--text-muted)", fontSize: 12, marginBottom: 10 }}>
                  Paste or compose a JSON payload to simulate a webhook delivery.
                </div>
                <div className="wf-test-payload-label">Payload (JSON)</div>
                <textarea
                  className="wf-test-payload-input"
                  value={manualPayload}
                  onChange={(e) => {
                    setManualPayload(e.target.value);
                    setManualError("");
                  }}
                  rows={8}
                  spellCheck={false}
                  placeholder='{"key": "value"}'
                />
                {manualError && (
                  <div style={{ color: "var(--danger, #e53935)", fontSize: 12, marginTop: 4 }}>
                    {manualError}
                  </div>
                )}
              </div>
            )}
          </div>
          <div className="wf-test-footer">
            <button className="wf-test-btn" onClick={onClose}>
              Cancel
            </button>
            {brokerTab === "consume" && state === "captured" && capturedPayload && (
              <button
                className="wf-test-btn primary"
                onClick={() => onRunWithPayload(capturedPayload!)}
              >
                Run with Payload
              </button>
            )}
            {brokerTab === "manual" && (
              <button
                className="wf-test-btn primary"
                onClick={handleBrokerRun}
              >
                Run with Payload
              </button>
            )}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="wf-test-overlay">
      <div className="wf-test-modal">
        <div className="wf-test-header">
          <h3>Test Trigger: {TRIGGER_TYPE_LABELS[triggerType]}</h3>
          <button className="wf-test-close" onClick={handleCancel}>
            {"\u2715"}
          </button>
        </div>
        <div className="wf-test-body">
          <div className="wf-test-status">
            <span className="wf-test-status-icon">
              {statusIcon[state]}
            </span>
            <span className="wf-test-status-text">{statusText[state]}</span>
            {state === "listening" && (
              <span className="wf-test-status-timer">
                {Math.floor(seconds / 60)}:{(seconds % 60)
                  .toString()
                  .padStart(2, "0")}
              </span>
            )}
          </div>

          {triggerInfo}
          {fsFilePicker}

          {state === "captured" && capturedPayload && (
            <>
              <div className="wf-test-payload-label">Captured Payload</div>
              <pre className="wf-test-payload">
                {JSON.stringify(capturedPayload, null, 2)}
              </pre>
            </>
          )}
        </div>
        <div className="wf-test-footer">
          <button className="wf-test-btn" onClick={handleCancel}>
            Cancel
          </button>
          {state === "captured" && (
            <button
              className="wf-test-btn primary"
              onClick={() => onRunWithPayload(capturedPayload!)}
            >
              Run with Payload
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
