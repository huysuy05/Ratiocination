import { fileURLToPath, URL } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, type ProxyOptions } from "vite";

/** Dev server port: `PORT` env (agents use their assigned port), default 5173 (UI/API docs). */
const DEFAULT_PORT = 5173;
/** Backend origin for the `/api` proxy (API.md "Non-API routes"). Override with RADAR_API_PROXY. */
const DEFAULT_API_TARGET = "http://127.0.0.1:8000";

function resolvePort(raw: string | undefined): number {
  const port = Number(raw);
  return Number.isInteger(port) && port > 0 && port < 65536 ? port : DEFAULT_PORT;
}

const port = resolvePort(process.env.PORT);
const apiTarget = process.env.RADAR_API_PROXY || DEFAULT_API_TARGET;

const proxy: Record<string, ProxyOptions> = {
  "/api": { target: apiTarget, changeOrigin: false },
};

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  // The graph route lazy-loads Cytoscape (~580 kB); nothing else comes close to this limit.
  build: { chunkSizeWarningLimit: 650 },
  server: { port, strictPort: true, proxy },
  preview: { port, strictPort: true, proxy },
});
