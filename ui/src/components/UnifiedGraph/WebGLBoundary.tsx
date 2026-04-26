/**
 * WebGLBoundary — probes WebGL availability and catches render-time errors
 * from ForceGraph3D so a missing GL context doesn't crash the whole app.
 *
 * Sandboxed environments (VS Code webview, headless Electron, GPU-disabled
 * Chromium) can refuse WebGL with "BindToCurrentSequence failed: Error
 * creating WebGL context". When that happens we show a friendly fallback
 * instead of an unhandled exception that breaks every other panel.
 */

import { Component, type ErrorInfo, type ReactNode } from "react";

interface ProbeResult {
  ok: boolean;
  reason?: string;
}

export function probeWebGL(): ProbeResult {
  try {
    if (typeof window === "undefined" || typeof document === "undefined") {
      return { ok: false, reason: "no DOM" };
    }
    if (!("WebGLRenderingContext" in window)) {
      return { ok: false, reason: "WebGL not supported by this runtime" };
    }
    const canvas = document.createElement("canvas");
    const ctx =
      (canvas.getContext("webgl2") as WebGLRenderingContext | null) ||
      (canvas.getContext("webgl") as WebGLRenderingContext | null) ||
      (canvas.getContext("experimental-webgl") as WebGLRenderingContext | null);
    if (!ctx) return { ok: false, reason: "GPU disabled or sandboxed (no WebGL context)" };
    // Release the probe context immediately.
    const lose = ctx.getExtension("WEBGL_lose_context");
    lose?.loseContext();
    return { ok: true };
  } catch (e) {
    return { ok: false, reason: e instanceof Error ? e.message : String(e) };
  }
}

interface BoundaryProps { fallback: (reason: string, retry: () => void) => ReactNode; children: ReactNode }
interface BoundaryState { error: string | null }

export class WebGLBoundary extends Component<BoundaryProps, BoundaryState> {
  state: BoundaryState = { error: null };

  static getDerivedStateFromError(err: Error): BoundaryState {
    return { error: err.message || "Unknown rendering error" };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // eslint-disable-next-line no-console
    console.error("[UnifiedGraph] 3D canvas error:", error, info);
  }

  retry = () => this.setState({ error: null });

  render() {
    if (this.state.error) return this.props.fallback(this.state.error, this.retry);
    return this.props.children;
  }
}

interface FallbackProps {
  reason: string;
  onRetry: () => void;
  nodeCount: number;
  edgeCount: number;
}

export function WebGLFallback({ reason, onRetry, nodeCount, edgeCount }: FallbackProps) {
  return (
    <div className="ug-webgl-fallback">
      <div className="ug-webgl-fallback-card">
        <h3>3D graph unavailable</h3>
        <p className="ug-webgl-fallback-reason">{reason}</p>
        <p>
          The host couldn't create a WebGL context. This usually means the
          embedded webview has GPU acceleration disabled or the OS GPU process
          is unhealthy.
        </p>
        <ul className="ug-webgl-fallback-tips">
          <li>Reload the window (the GPU process may recover).</li>
          <li>If you're inside a sandboxed VS Code/Electron view, try opening Nexus in a regular browser.</li>
          <li>Check that the host hasn't been launched with <code>--disable-gpu</code>.</li>
        </ul>
        <p className="ug-webgl-fallback-stats">
          Graph data is loaded ({nodeCount} nodes, {edgeCount} edges) — only the 3D rendering is unavailable.
        </p>
        <button className="ug-tool-btn" onClick={onRetry}>Try again</button>
      </div>
    </div>
  );
}
