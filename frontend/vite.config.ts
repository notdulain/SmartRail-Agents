import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Dev server proxies /api to the backend. Override the port per worktree with SMARTRAIL_PORT.
const backendPort = process.env.SMARTRAIL_PORT ?? "8765";
const devPort = Number(process.env.SMARTRAIL_DEV_PORT ?? "5173");

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: "127.0.0.1",
    port: devPort,
    proxy: { "/api": `http://127.0.0.1:${backendPort}` },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    globals: true,
  },
});
