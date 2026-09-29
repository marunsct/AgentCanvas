import { defineConfig } from "@playwright/test";

// Starts the fake-model API (no keys needed) and the Vite dev server, then drives the UI.
// In the Claude Code / CI sandbox Chromium lives in /opt/pw-browsers; locally Playwright finds its own.
const executablePath = process.env.PW_CHROMIUM_PATH;

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  use: { baseURL: "http://localhost:5173", viewport: { width: 1300, height: 800 }, launchOptions: executablePath ? { executablePath } : {} },
  webServer: [
    {
      command: "uv run uvicorn agentcanvas.dev.fake_server:app --port 8000",
      cwd: "../api",
      url: "http://localhost:8000/healthz",
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
    { command: "pnpm dev --port 5173 --strictPort", url: "http://localhost:5173", reuseExistingServer: !process.env.CI, timeout: 120_000 },
  ],
});
