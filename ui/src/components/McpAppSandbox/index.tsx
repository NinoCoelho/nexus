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

interface Props {
  html: string;
  toolResult?: unknown;
  onToolCall?: (name: string, args: Record<string, unknown>) => Promise<unknown>;
}

const SANDBOX_ATTRS = "allow-scripts";

export default function McpAppSandbox({ html, toolResult, onToolCall }: Props) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState(200);

  const sendToApp = useCallback((message: Record<string, unknown>) => {
    const iframe = iframeRef.current;
    if (!iframe?.contentWindow) return;
    iframe.contentWindow.postMessage(message, "*");
  }, []);

  // Push tool result to the app when it arrives
  useEffect(() => {
    if (toolResult != null) {
      sendToApp({
        jsonrpc: "2.0",
        method: "ui/toolResult",
        params: { result: toolResult },
      });
    }
  }, [toolResult, sendToApp]);

  // Handle messages from the app (tool calls, resize, etc.)
  useEffect(() => {
    const handler = async (event: MessageEvent) => {
      const iframe = iframeRef.current;
      if (!iframe || event.source !== iframe.contentWindow) return;
      const data = event.data;
      if (!data || typeof data !== "object") return;
      if (data.jsonrpc !== "2.0") return;

      const method = data.method as string | undefined;
      const id = data.id as string | number | undefined;
      const params = data.params as Record<string, unknown> | undefined;

      if (method === "tools/call" && params && onToolCall) {
        const toolName = params.name as string;
        const args = (params.arguments as Record<string, unknown>) || {};
        try {
          const result = await onToolCall(toolName, args);
          sendToApp({ jsonrpc: "2.0", id, result: { content: [{ type: "text", text: JSON.stringify(result) }] } });
        } catch (err) {
          sendToApp({
            jsonrpc: "2.0",
            id,
            error: { code: -32000, message: err instanceof Error ? err.message : "Tool call failed" },
          });
        }
        return;
      }

      // ui/resize — app requests a different height
      if (method === "ui/resize" && params?.height && typeof params.height === "number") {
        setHeight(Math.min(params.height, 800));
        return;
      }

      // ui/initialized — app is ready
      if (method === "ui/initialized") {
        if (toolResult != null) {
          sendToApp({
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
  }, [onToolCall, sendToApp, toolResult]);

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
    <div className="mcp-app-container" style={{ border: "1px solid var(--color-border, #e0e0e0)", borderRadius: "6px", overflow: "hidden" }}>
      <iframe
        ref={iframeRef}
        sandbox={SANDBOX_ATTRS}
        srcDoc={srcDoc}
        style={{ width: "100%", height: `${height}px`, border: "none", display: "block" }}
        title="MCP App"
      />
    </div>
  );
}
