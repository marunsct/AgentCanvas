import { answerDialog, expect, runnableWorkflow, test } from "./fixtures";

// Sprint 12 (FR-EVAL-02..04): build a dataset and an evaluator, run an experiment on the draft and a variant,
// read the grid, open a run's timeline, and compare two experiments.

test("dataset, evaluator, experiment with a variant, results grid and comparison", async ({ page }) => {
  const api = page.request;
  const wf = await (await api.post("/api/v1/projects/prj_default/workflows", { data: { name: `Eval ${Date.now()}` } })).json();
  const ir = {
    ...wf.ir, state: { channels: [{ name: "who" }, { name: "out" }] },
    nodes: [{ id: "n_g", type: "core.set", name: "greet", config: { values: { out: "Hello {{ state.who }}" } } }],
    edges: [
      { id: "e1", kind: "flow", source: { node: "START" }, target: { node: "n_g" } },
      { id: "e2", kind: "flow", source: { node: "n_g" }, target: { node: "END" } },
    ],
  };
  expect((await api.put(`/api/v1/workflows/${wf.id}`, { data: { ir }, headers: { "If-Match": "1" } })).ok()).toBeTruthy();

  await page.goto(`/workflows/${wf.id}`);
  await page.getByTestId("evaluation-link").click();
  await page.getByLabel("dataset name").fill("Greetings");
  await page.getByRole("button", { name: "Create dataset" }).click();
  const view = page.getByTestId("dataset-view");
  for (const who of ["Ada", "Bob"]) {
    await view.getByLabel("input who").fill(who); // a field per workflow field, not JSON
    await view.getByLabel("expected out").fill(`Hello ${who}`);
    await view.getByRole("button", { name: "Add example" }).click();
    await expect(view.getByTestId("examples")).toContainText(who);
  }

  await page.getByRole("tab", { name: "Evaluators" }).click();
  await page.getByRole("button", { name: "+ New evaluator" }).click();
  await page.getByLabel("evaluator name").fill("exact");
  await page.getByRole("button", { name: "Add evaluator" }).click();
  await expect(page.getByTestId("evaluators")).toContainText("exact");
  // a kind with settings: a form from its schema, not JSON; then edited in place
  await page.getByRole("button", { name: "+ New evaluator" }).click();
  await page.getByLabel("evaluator name").fill("says hello");
  await page.getByLabel("evaluator kind").selectOption("regex");
  const form = page.getByRole("form", { name: "New evaluator" });
  await form.getByLabel("Channel", { exact: false }).first().fill("out");
  await form.getByLabel("Pattern", { exact: false }).first().fill("^Hello");
  await page.getByRole("button", { name: "Add evaluator" }).click();
  const row = page.getByTestId("evaluators").locator("tr", { hasText: "says hello" });
  await expect(row).toContainText("Pattern");
  await row.getByRole("button", { name: "Edit" }).click();
  await page.getByRole("form", { name: "Edit evaluator" }).getByLabel("Pattern", { exact: false }).first().fill("Hello");
  await page.getByRole("button", { name: "Save evaluator" }).click();
  await expect(row).toContainText('"pattern":"Hello"');
  await page.getByTestId("evaluators").locator("tr", { hasText: "Exact match" }).getByRole("button", { name: "Try" }).click();
  await page.getByLabel("sample").fill('{"output": {"out": "Hi"}, "reference": {"out": "Hi"}}');
  await page.getByRole("button", { name: "Score this sample" }).click();
  await expect(page.getByTestId("try-evaluator")).toContainText("exact 1.00");

  await page.getByRole("tab", { name: "Experiments" }).click();
  await page.getByRole("button", { name: "▶ Run experiment" }).click();
  await expect(page.getByTestId("experiments")).toContainText("done");
  await page.getByRole("button", { name: "+ Add a version or variant" }).click();
  await page.getByLabel("target 2 label").fill("hi");
  const variant = page.getByRole("group", { name: "target 2" }); // the variant's changes, as rows
  await variant.getByRole("button", { name: "+ Change a setting" }).click();
  await variant.getByLabel("target 2 change 1 block").selectOption("greet");
  await variant.getByLabel("target 2 change 1 setting").selectOption("values");
  await variant.getByLabel("target 2 change 1 value").fill('{"out": "Hi {{ state.who }}"}');
  await page.getByRole("button", { name: "▶ Run experiment" }).click();
  const results = page.getByTestId("results");
  await expect(results).toContainText("Hi Ada");
  await expect(results.locator("thead")).toContainText("100%");
  await expect(results.locator("thead")).toContainText("0%");
  await expect(page.getByTestId("experiments").locator("tr", { hasText: "running" })).toHaveCount(0);

  await page.getByLabel("baseline experiment").selectOption({ index: 1 });
  await expect(page.getByTestId("comparison")).toContainText("exact");
  await expect(page.getByTestId("comparison")).toContainText("Regressed: none");

  await results.getByRole("button", { name: "#1" }).click();
  await expect(page.getByTestId("output-diff")).toContainText("out ≠");
  await results.getByRole("link", { name: "timeline" }).first().click();
  await expect(page).toHaveURL(new RegExp(`/workflows/${wf.id}\\?thread=th_`));
  await expect(page.getByRole("tab", { name: "Timeline" })).toHaveAttribute("aria-selected", "true");
});

test("a release gate refuses a regression; an admin publishes over it with a reason", async ({ page }) => {
  const api = page.request;
  const wf = await (await api.post("/api/v1/projects/prj_default/workflows", { data: { name: `Gate ${Date.now()}` } })).json();
  const ir = (greeting: string) => ({
    ...wf.ir, state: { channels: [{ name: "who" }, { name: "out" }] },
    nodes: [{ id: "n_g", type: "core.set", name: "greet", config: { values: { out: `${greeting} {{ state.who }}` } } }],
    edges: [
      { id: "e1", kind: "flow", source: { node: "START" }, target: { node: "n_g" } },
      { id: "e2", kind: "flow", source: { node: "n_g" }, target: { node: "END" } },
    ],
  });
  expect((await api.put(`/api/v1/workflows/${wf.id}`, { data: { ir: ir("Hello") }, headers: { "If-Match": "1" } })).ok()).toBeTruthy();
  const ds = await (await api.post(`/api/v1/workflows/${wf.id}/datasets`, { data: { name: "Smoke" } })).json();
  await api.post(`/api/v1/datasets/${ds.id}/examples`, { data: { examples: [{ input: { who: "Ada" }, reference: { out: "Hello Ada" } }] } });
  await api.post(`/api/v1/workflows/${wf.id}/evaluators`, { data: { name: "exact", kind: "exact", config: {} } });

  await page.goto(`/workflows/${wf.id}/evaluation`);
  await page.getByRole("tab", { name: "Release gates" }).click();
  await page.getByLabel("minimum exact").fill("0.9");
  await page.getByRole("button", { name: "Save gate" }).click();
  await expect(page.getByTestId("gates")).toContainText("exact ≥ 0.9");

  expect((await api.put(`/api/v1/workflows/${wf.id}`, { data: { ir: ir("Hey") }, headers: { "If-Match": "2" } })).ok()).toBeTruthy();
  await page.goto(`/workflows/${wf.id}`);
  await page.getByRole("tab", { name: "Versions" }).click();
  await page.getByRole("button", { name: "Publish to prod" }).click();
  await expect(page.getByTestId("publish-checklist")).toContainText("exact: mean 0.0 (needs 0.9)");
  await page.getByRole("button", { name: "Publish anyway…" }).click();
  await answerDialog(page, "demo for the partner call");
  await expect(page.getByTestId("publish-checklist")).toContainText("overridden by");
  await expect(page.getByTestId("deployment-prod")).toBeVisible();
});

test("an annotation queue: a rubric as rows, runs picked from a list, a review", async ({ page }) => {
  const api = page.request;
  const wf = await runnableWorkflow(api, `Queue ${Date.now()}`);
  expect((await api.post(`/api/v1/workflows/${wf.id}/runs/stream`, { data: { input: {} } })).ok()).toBeTruthy();
  await page.goto(`/workflows/${wf.id}/evaluation`);
  await page.getByRole("tab", { name: "Annotation queues" }).click();
  const form = page.getByTestId("queue-form");
  await form.getByLabel("queue name").fill("Weekly review");
  await form.getByLabel("criterion 2 name").fill("accurate");
  await form.getByRole("button", { name: "+ Add a criterion" }).click();
  await form.getByLabel("criterion 3 name").fill("tone");
  await form.getByLabel("criterion 3 scale").selectOption("3");
  await form.getByRole("button", { name: "Create queue" }).click();

  const review = page.getByTestId("queue-review");
  await review.getByText("Add runs by hand").click();
  await review.locator(".run-picker input[type=checkbox]").first().check();
  await review.getByRole("button", { name: /^Add 1 run$/ }).click();
  await expect(page.getByTestId("toasts")).toContainText("Added 1 run");
  for (const [name, choice] of [["helpful", "4"], ["accurate", "yes"], ["tone", "3"]]) {
    await review.getByRole("radiogroup", { name }).getByLabel(choice, { exact: true }).check();
  }
  await review.getByRole("button", { name: "Submit review" }).click();
  await expect(page.getByTestId("queues")).toContainText("1 of 1");
});

test("an experiment runs each example twice: pass@2, pass^2 and a row per run", async ({ page }) => {
  // F-6: runs per example. The greeter answers the same every time, so pass@2 and pass^2 agree and nothing is flaky.
  const api = page.request;
  const wf = await (await api.post("/api/v1/projects/prj_default/workflows", { data: { name: `Repeat ${Date.now()}` } })).json();
  const ir = {
    ...wf.ir, state: { channels: [{ name: "who" }, { name: "out" }] },
    nodes: [{ id: "n_g", type: "core.set", name: "greet", config: { values: { out: "Hello {{ state.who }}" } } }],
    edges: [
      { id: "e1", kind: "flow", source: { node: "START" }, target: { node: "n_g" } },
      { id: "e2", kind: "flow", source: { node: "n_g" }, target: { node: "END" } },
    ],
  };
  expect((await api.put(`/api/v1/workflows/${wf.id}`, { data: { ir }, headers: { "If-Match": "1" } })).ok()).toBeTruthy();
  const ds = await (await api.post(`/api/v1/workflows/${wf.id}/datasets`, { data: { name: "Twice" } })).json();
  const examples = ["Ada", "Bob"].map((who) => ({ input: { who }, reference: { out: `Hello ${who}` } }));
  expect((await api.post(`/api/v1/datasets/${ds.id}/examples`, { data: { examples } })).ok()).toBeTruthy();
  expect((await api.post(`/api/v1/workflows/${wf.id}/evaluators`, { data: { name: "exact", kind: "exact", config: {} } })).ok()).toBeTruthy();

  await page.goto(`/workflows/${wf.id}/evaluation`);
  await page.getByRole("tab", { name: "Experiments" }).click();
  await page.getByLabel("runs per example").fill("2");
  await page.getByRole("button", { name: "▶ Run experiment" }).click();
  const results = page.getByTestId("results");
  await expect(results.getByTestId("repeated")).toContainText("pass@2 100%");
  await expect(results.getByTestId("repeated")).toContainText("pass^2 100%");
  await expect(results.getByTestId("repeated")).not.toContainText("flaky");
  await expect(results.getByRole("button", { name: /#1 · run 2/ })).toBeVisible();
});
