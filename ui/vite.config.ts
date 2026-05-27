import type { ServerOptions } from "vite";
import type { IncomingMessage, ServerResponse } from "node:http";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const API_TARGET = "http://localhost:18989";
const API_PREFIXES = [
  "/chat", "/sessions", "/vault", "/skills", "/config", "/providers",
  "/catalog", "/auth", "/models", "/routing", "/graph", "/graphrag",
  "/share", "/local", "/notifications", "/push", "/transcribe", "/audio",
  "/health", "/heartbeat", "/cookies", "/dream", "/mcp", "/jobs",
  "/update", "/workflows", "/projects", "/tunnel", "/broker", "/webhook",
  "/settings",
];

function stripProxyHeaders(
  proxyReq: { removeHeader(name: string): void },
  _req: IncomingMessage,
  _res: ServerResponse,
) {
  proxyReq.removeHeader("x-forwarded-for");
  proxyReq.removeHeader("x-forwarded-host");
  proxyReq.removeHeader("cf-ray");
  proxyReq.removeHeader("cf-connecting-ip");
  proxyReq.removeHeader("ngrok-trace-id");
}

function proxyTarget(): ServerOptions["proxy"] {
  return Object.fromEntries(
    API_PREFIXES.map(p => [
      p,
      {
        target: API_TARGET,
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on("proxyReq", stripProxyHeaders);
        },
      },
    ])
  );
}

export default defineConfig({
  plugins: [react()],
  server: {
    port: 1890,
    allowedHosts: [".nexus-model.us", ".trycloudflare.com"],
    proxy: proxyTarget(),
  },
});
