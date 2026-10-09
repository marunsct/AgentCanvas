import { defineConfig } from "@playwright/test";

// Starts the fake-model API (no keys needed) and the Vite dev server, then drives the UI.
// In the Claude Code / CI sandbox Chromium lives in /opt/pw-browsers; locally Playwright finds its own.
const executablePath = process.env.PW_CHROMIUM_PATH;

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  use: {
    baseURL: "http://localhost:5173", viewport: { width: 1300, height: 800 },
    launchOptions: executablePath ? { executablePath } : {},
    trace: "retain-on-failure", screenshot: "only-on-failure",
  },
  webServer: [
    {
      command: "uv run uvicorn agentcanvas.dev.fake_server:app --port 8000",
      cwd: "../api",
      env: { AGENTCANVAS_ADAPTERS: "langchain,echo" }, // S20: a second framework to switch to
      url: "http://localhost:8000/healthz",
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
    // CI (or E2E_PROD=1) drives the production build under the production Content-Security-Policy (no eval, no
    // inline script); locally the dev server is faster to iterate with
    process.env.CI || process.env.E2E_PROD
      ? { command: "pnpm build && pnpm preview --port 5173 --strictPort", url: "http://localhost:5173", reuseExistingServer: false, timeout: 300_000 }
      : { command: "pnpm dev --port 5173 --strictPort", url: "http://localhost:5173", reuseExistingServer: true, timeout: 120_000 },
  ],
});
