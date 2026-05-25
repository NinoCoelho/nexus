import { useEffect, useRef, useState, useCallback } from "react";
import {
  testTriggerListenUrl,
  cancelTestListener,
  testTrigger,
} from "../../api/workflows";
import type { TriggerType, TriggerConfig } from "../../types/workflow";

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
};

interface Props {
  wfPath: string;
  triggerId: string;
  triggerType: TriggerType;
  triggerConfig: TriggerConfig;
  onClose: () => void;
  onRunWithPayload: (payload: Record<string, unknown>) => void;
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

export default function TriggerTestModal({
  wfPath,
  triggerId,
  triggerType,
  triggerConfig: _triggerConfig,
  onClose,
  onRunWithPayload,
}: Props) {
  const [state, setState] = useState<ModalState>(
    triggerType === "schedule" ? "captured" : "connecting",
  );
  const [capturedPayload, setCapturedPayload] = useState<
    Record<string, unknown> | null
  >(triggerType === "schedule" ? null : null);
  const [listenInfo, setListenInfo] = useState<{
    url?: string;
    path?: string;
    pattern?: string;
  }>({});
  const [error, setError] = useState("");
  const [seconds, setSeconds] = useState(60);
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
    if (triggerType === "schedule") return;

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

  if (triggerType === "schedule") {
    return (
      <div className="wf-test-overlay">
        <div className="wf-test-modal">
          <div className="wf-test-header">
            <h3>Test Trigger: {TRIGGER_TYPE_LABELS[triggerType]}</h3>
            <button className="wf-test-close" onClick={onClose}>
              \u2715
            </button>
          </div>
          <div className="wf-test-body">
            <div className="wf-test-status">
              <span className="wf-test-status-icon">{"\u2705"}</span>
              <span className="wf-test-status-text">
                Ready to run with generated schedule payload
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
