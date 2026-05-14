/**
 * McpAppSandbox — renders MCP App HTML inside a sandboxed iframe.
 *
 * Follows the MCP Apps specification: the iframe runs with
 * `sandbox="allow-scripts"` and communicates with the host via
 * postMessage JSON-RPC.
 *
 * The host can push tool results and the app can call MCP tools
 * through the bridge.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import "./McpAppSandbox.css";

interface Props {
  html: string;
  toolResult?: unknown;
  onToolCall?: (name: string, args: Record<string, unknown>) => Promise<unknown>;
}

const SANDBOX_ATTRS = "allow-scripts";

export default function McpAppSandbox({ html, toolResult, onToolCall }: Props) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const zoomIframeRef = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState(400);
  const [zoomed, setZoomed] = useState(false);

  const sendToApp = useCallback(
    (iframe: HTMLIFrameElement | null, message: Record<string, unknown>) => {
      if (!iframe?.contentWindow) return;
      iframe.contentWindow.postMessage(message, "*");
    },
    [],
  );

  useEffect(() => {
    if (toolResult != null) {
      sendToApp(iframeRef.current, {
        jsonrpc: "2.0",
        method: "ui/toolResult",
        params: { result: toolResult },
      });
      if (zoomed) {
        sendToApp(zoomIframeRef.current, {
          jsonrpc: "2.0",
          method: "ui/toolResult",
          params: { result: toolResult },
        });
      }
    }
  }, [toolResult, sendToApp, zoomed]);

  useEffect(() => {
    const handler = async (event: MessageEvent) => {
      const iframe = iframeRef.current;
      const zoomIframe = zoomIframeRef.current;
      const source = event.source as Window | null;
      const isMain = iframe && source === iframe.contentWindow;
      const isZoom = zoomed && zoomIframe && source === zoomIframe.contentWindow;
      if (!isMain && !isZoom) return;

      const data = event.data;
      if (!data || typeof data !== "object") return;
      if (data.jsonrpc !== "2.0") return;

      const method = data.method as string | undefined;
      const id = data.id as string | number | undefined;
      const params = data.params as Record<string, unknown> | undefined;
      const target = isMain ? iframe : zoomIframe;

      if (method === "tools/call" && params && onToolCall) {
        const toolName = params.name as string;
        const args = (params.arguments as Record<string, unknown>) || {};
        try {
          const result = await onToolCall(toolName, args);
          sendToApp(target, { jsonrpc: "2.0", id, result: { content: [{ type: "text", text: JSON.stringify(result) }] } });
        } catch (err) {
          sendToApp(target, {
            jsonrpc: "2.0",
            id,
            error: { code: -32000, message: err instanceof Error ? err.message : "Tool call failed" },
          });
        }
        return;
      }

      if (method === "ui/resize" && params?.height && typeof params.height === "number") {
        setHeight(Math.min(params.height, window.innerHeight * 0.9));
        return;
      }

      if (method === "ui/initialized") {
        if (toolResult != null) {
          sendToApp(target, {
            jsonrpc: "2.0",
            method: "ui/toolResult",
            params: { result: toolResult },
          });
        }
        return;
      }
    };

    window.addEventListener("message", handler);
    return () => window.removeEventListener("message", handler);
  }, [onToolCall, sendToApp, toolResult, zoomed]);

  useEffect(() => {
    if (!zoomed) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setZoomed(false);
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [zoomed]);

  const srcDoc = `
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body { margin: 0; padding: 8px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }
  </style>
</head>
<body>
${html}
<script>
(function() {
  // MCP App bridge — communicates with the host via postMessage
  const pending = new Map();
  let callId = 0;

  function send(msg) {
    window.parent.postMessage(msg, '*');
  }

  // Listen for responses from the host
  window.addEventListener('message', function(e) {
    const data = e.data;
    if (!data || data.jsonrpc !== '2.0') return;
    if (data.id != null && pending.has(data.id)) {
      const { resolve, reject } = pending.get(data.id);
      pending.delete(data.id);
      if (data.error) reject(new Error(data.error.message));
      else resolve(data.result);
    }
    // Handle tool result push from host
    if (data.method === 'ui/toolResult' && window.__mcpAppOnResult) {
      window.__mcpAppOnResult(data.params?.result);
    }
  });

  // Notify host that we're ready
  send({ jsonrpc: '2.0', method: 'ui/initialized' });

  // Expose callServerTool to the app
  window.callServerTool = function(toolCall) {
    return new Promise(function(resolve, reject) {
      var id = ++callId;
      pending.set(id, { resolve: resolve, reject: reject });
      send({ jsonrpc: '2.0', id: id, method: 'tools/call', params: toolCall });
    });
  };
})();
</script>
</body>
</html>`;

  return (
    <div className="mcp-app-container">
      <button
        className="mcp-app-zoom-btn"
        onClick={() => setZoomed(true)}
        title="Expand"
        aria-label="Expand MCP app"
      >
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <polyline points="4 4 1 4 1 1 4 1" />
          <polyline points="12 4 15 4 15 1 12 1" />
          <polyline points="4 12 1 12 1 15 4 15" />
          <polyline points="12 12 15 12 15 15 12 15" />
        </svg>
      </button>
      <iframe
        ref={iframeRef}
        sandbox={SANDBOX_ATTRS}
        srcDoc={srcDoc}
        style={{ width: "100%", height: `${height}px`, border: "none", display: "block" }}
        title="MCP App"
      />
      {zoomed && (
        <div className="mcp-app-zoom-overlay" onClick={() => setZoomed(false)}>
          <div className="mcp-app-zoom-frame" onClick={(e) => e.stopPropagation()}>
            <div className="mcp-app-zoom-header">
              <span>MCP App</span>
              <button className="mcp-app-zoom-close" onClick={() => setZoomed(false)} aria-label="Close">
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
                  <line x1="3" y1="3" x2="13" y2="13" />
                  <line x1="13" y1="3" x2="3" y2="13" />
                </svg>
              </button>
            </div>
            <iframe
              ref={zoomIframeRef}
              sandbox={SANDBOX_ATTRS}
              srcDoc={srcDoc}
              className="mcp-app-zoom-iframe"
              title="MCP App (expanded)"
            />
          </div>
        </div>
      )}
    </div>
  );
}
