import { expect, test } from "@playwright/test";

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
    await view.getByLabel("example input").fill(JSON.stringify({ who }));
    await view.getByLabel("example reference").fill(JSON.stringify({ out: `Hello ${who}` }));
    await view.getByRole("button", { name: "Add example" }).click();
    await expect(view.getByTestId("examples")).toContainText(who);
  }

  await page.getByRole("tab", { name: "Evaluators" }).click();
  await page.getByLabel("evaluator name").fill("exact");
  await page.getByRole("button", { name: "Add evaluator" }).click();
  await expect(page.getByTestId("evaluators")).toContainText("exact");
  await page.getByRole("button", { name: "Try" }).click();
  await page.getByLabel("sample").fill('{"output": {"out": "Hi"}, "reference": {"out": "Hi"}}');
  await page.getByRole("button", { name: "Score this sample" }).click();
  await expect(page.getByTestId("try-evaluator")).toContainText("exact 1.00");

  await page.getByRole("tab", { name: "Experiments" }).click();
  await page.getByRole("button", { name: "▶ Run experiment" }).click();
  await expect(page.getByTestId("experiments")).toContainText("done");
  await page.getByRole("button", { name: "+ Add a version or variant" }).click();
  await page.getByLabel("target 2 label").fill("hi");
  await page.getByLabel("target 2 overrides").fill('{"greet": {"values": {"out": "Hi {{ state.who }}"}}}');
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
