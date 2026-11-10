import { defineConfig } from "@playwright/test";

// Starts the fake-model API (no keys needed) and the Vite dev server, then drives the UI.
// In the Claude Code / CI sandbox Chromium lives in /opt/pw-browsers; locally Playwright finds its own.
const executablePath = process.env.PW_CHROMIUM_PATH;
// The fake-model API: port 8000 unless E2E_API_PORT says otherwise (another program may hold 8000; a server that is
// already there is reused, so a wrong one on the port would answer every request)
const apiPort = process.env.E2E_API_PORT ?? "8000";

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
      // a database and workspaces of its own, emptied before each run: what one run leaves (a journey that failed
      // half-way in Simple mode) must not fail the next, and `make dev-fake`'s own data is left alone
      command: `rm -rf .e2e && mkdir .e2e && uv run uvicorn agentcanvas.dev.fake_server:app --port ${apiPort}`,
      cwd: "../api",
      env: {
        AGENTCANVAS_ADAPTERS: "langchain,echo,openai_agents,crewai,google_adk,pydantic_ai,agno,microsoft_agent_framework,strands,llamaindex,mastra", // S20-S36
        DATABASE_URL: "sqlite+aiosqlite:///./.e2e/agentcanvas.db",
        AGENTCANVAS_WORKSPACES_DIR: "./.e2e/workspaces",
      },
      url: `http://localhost:${apiPort}/healthz`,
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
