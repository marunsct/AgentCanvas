import { expect, test } from "@playwright/test";

// Sprint 8 (M6-T1, M6-T9): save a run as a test case, version the workflow, break it, see red + the diff.

test("saved test case turns red after an edit; the diff shows the edited step", async ({ page }) => {
  const api = page.request;
  const wf = await (await api.post("/api/v1/projects/prj_default/workflows", { data: { name: `Release ${Date.now()}` } })).json();
  const ir = (out: string) => ({
    ...wf.ir, state: { channels: [{ name: "who" }, { name: "out" }] },
    nodes: [{ id: "n_g", type: "core.set", name: "greet", config: { values: { out } } }],
    edges: [
      { id: "e1", kind: "flow", source: { node: "START" }, target: { node: "n_g" } },
      { id: "e2", kind: "flow", source: { node: "n_g" }, target: { node: "END" } },
    ],
  });
  expect((await api.put(`/api/v1/workflows/${wf.id}`, { data: { ir: ir("Hi {{ state.who }}") }, headers: { "If-Match": "1" } })).ok()).toBeTruthy();

  await page.goto(`/workflows/${wf.id}`);
  await page.getByRole("tab", { name: "Run" }).click();
  await page.getByLabel("run input").fill('{"who": "Ada"}');
  await page.getByRole("button", { name: "▶ Run" }).click();
  await expect(page.getByTestId("run-output")).toContainText("Hi Ada");
  page.once("dialog", (d) => void d.accept("greets Ada"));
  await page.getByRole("button", { name: "Save as test case" }).click();
  await expect(page.getByRole("status").filter({ hasText: "saved" })).toBeVisible();

  await page.getByRole("tab", { name: "Tests" }).click();
  await page.getByRole("button", { name: "▶ Run tests" }).click();
  await expect(page.getByTestId("test-summary")).toHaveText("1 passed · 0 failed");

  await page.getByRole("tab", { name: "Versions" }).click();
  await page.getByLabel("version message").fill("first");
  await page.getByRole("button", { name: "Save version" }).click();
  await expect(page.getByTestId("version-1.0.0")).toContainText("first");

  const cur = await (await api.get(`/api/v1/workflows/${wf.id}`)).json();
  await api.put(`/api/v1/workflows/${wf.id}`, { data: { ir: ir("Bye {{ state.who }}") }, headers: { "If-Match": String(cur.version) } });
  await page.reload();
  await page.getByRole("tab", { name: "Tests" }).click();
  await page.getByRole("button", { name: "▶ Run tests" }).click();
  await expect(page.getByTestId("test-summary")).toHaveText("0 passed · 1 failed");
  await expect(page.getByTestId("case-greets Ada")).toContainText("channel 'out'");

  await page.getByRole("tab", { name: "Versions" }).click();
  await page.getByTestId("version-1.0.0").getByRole("button", { name: "Compare" }).click();
  await expect(page.getByTestId("diff-summary")).toContainText("1 step changed");
  await expect(page.getByTestId("node-greet")).toHaveClass(/diff-changed/);
  await expect(page.getByTestId("contract-diff")).toContainText("Compatible with v1.0.0");
});

test("export the draft as a zip", async ({ page }) => {
  const api = page.request;
  const wf = await (await api.post("/api/v1/projects/prj_default/workflows", { data: { name: `Export ${Date.now()}` } })).json();
  await page.goto(`/workflows/${wf.id}`);
  await page.getByRole("tab", { name: "Versions" }).click();
  const download = page.waitForEvent("download");
  await page.getByTestId("export-draft").click();
  expect((await download).suggestedFilename()).toMatch(/^export-\d+\.zip$/);
});

test("publish from the Versions tab, then call the endpoint with the key", async ({ page }) => {
  const api = page.request;
  const wf = await (await api.post("/api/v1/projects/prj_default/workflows", { data: { name: `Publish ${Date.now()}` } })).json();
  const ir = {
    ...wf.ir, state: { channels: [{ name: "who" }, { name: "out" }] },
    nodes: [{ id: "n_g", type: "core.set", name: "greet", config: { values: { out: "Hi {{ state.who }}" } } }],
    edges: [
      { id: "e1", kind: "flow", source: { node: "START" }, target: { node: "n_g" } },
      { id: "e2", kind: "flow", source: { node: "n_g" }, target: { node: "END" } },
    ],
  };
  await api.put(`/api/v1/workflows/${wf.id}`, { data: { ir }, headers: { "If-Match": "1" } });
  await page.goto(`/workflows/${wf.id}`);
  await page.getByRole("tab", { name: "Versions" }).click();
  await page.getByRole("button", { name: "Publish to prod" }).click();
  await expect(page.getByTestId("publish-checklist")).toContainText("no test cases");
  await expect(page.getByTestId("deployment-prod")).toContainText("v1.0.0");
  const key = (await page.getByTestId("api-key").locator("code").textContent())!.trim();
  const deps = await (await api.get(`/api/v1/workflows/${wf.id}/deployments`)).json();
  const res = await api.post(`/api/v1/deployments/${deps[0].id}/runs/wait`, {
    data: { input: { who: "Ada" } }, headers: { Authorization: `Bearer ${key}` },
  });
  expect((await res.json()).output.out).toBe("Hi Ada");
});

test("add a webhook and a cron trigger", async ({ page }) => {
  const api = page.request;
  const wf = await (await api.post("/api/v1/projects/prj_default/workflows", { data: { name: `Triggers ${Date.now()}` } })).json();
  await page.goto(`/workflows/${wf.id}`);
  await page.getByRole("tab", { name: "Versions" }).click();
  await page.getByRole("button", { name: "+ Webhook" }).click();
  await expect(page.getByTestId("webhook-secret")).toContainText("whsec_");
  await page.getByLabel("cron schedule").fill("*/5 * * * *");
  await page.getByRole("button", { name: "+ Cron" }).click();
  await expect(page.getByTestId("trigger-cron")).toContainText("*/5 * * * *");
  await page.getByLabel("cron schedule").fill("nope");
  await page.getByRole("button", { name: "+ Cron" }).click();
  await expect(page.getByRole("alert")).toContainText("5 fields");
  await page.getByTestId("trigger-cron").getByRole("button", { name: "Disable" }).click();
  await expect(page.getByTestId("trigger-cron")).toContainText("off");
});

test("blast radius overlay and dry-run toggle", async ({ page }) => {
  const api = page.request;
  const wf = await (await api.post("/api/v1/projects/prj_default/workflows", { data: { name: `Blast ${Date.now()}`, template: "ticket_triage" } })).json();
  await page.goto(`/workflows/${wf.id}`);
  await page.getByRole("button", { name: "Blast radius" }).click();
  await expect(page.getByTestId("blast-classify")).toHaveText("$"); // model calls spend money
  await page.getByRole("tab", { name: "Run" }).click();
  await page.getByLabel("dry run").check();
  await expect(page.getByTestId("debug-badge")).toContainText("dry run");
});
