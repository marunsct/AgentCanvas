import { expect, test } from "./fixtures";

// The Copilot (S14): ask it to build, see the proposal on the canvas, accept it, and undo it in one step.

test("the Copilot builds a workflow the person accepts, and undo takes it back", async ({ page }) => {
  const settings = await page.request.put("/api/v1/projects/prj_default/copilot-settings", {
    data: { source: "separate", model: "anthropic:claude-haiku-4-5" },
  });
  expect(settings.ok()).toBeTruthy();
  await page.goto("/");
  await page.getByLabel("workflow name").fill(`Copilot ${Date.now()}`);
  await page.getByRole("button", { name: "Create empty" }).click();

  await page.getByTestId("copilot-button").click();
  await expect(page.getByTestId("copilot-panel")).toContainText("Claude Haiku 4.5");
  await page.getByLabel("Message the Copilot").fill("Build a workflow that greets people");
  await page.getByRole("button", { name: "Send" }).click();

  // the proposal is previewed on the canvas, nothing saved yet
  await expect(page.getByTestId("copilot-proposal")).toContainText("Add a Greet step");
  await expect(page.getByTestId("ghost-banner")).toBeVisible();
  await expect(page.getByTestId("node-greet")).toBeVisible();
  await expect(page.getByTestId("copilot-panel")).toContainText("I proposed a Greet step");

  await page.getByTestId("copilot-proposal").getByRole("button", { name: "Accept" }).click();
  await expect(page.getByTestId("ghost-banner")).toHaveCount(0);
  await expect(page.locator(".save-state")).toHaveText("Saved");
  await expect(page.getByTestId("node-greet")).toBeVisible();
  await expect(page.getByTestId("copilot-proposal")).toHaveCount(0);

  // a version saved now credits the Copilot and the person who accepted (FR-COP-07)
  await page.getByRole("tab", { name: "Versions" }).click();
  await page.getByLabel("version message").fill("greeter");
  await page.getByRole("button", { name: "Save version" }).click();
  await expect(page.getByTestId("copilot-credit")).toContainText("Copilot (accepted by");
  await expect(page.getByTestId("copilot-credit")).toContainText("Add a Greet step");

  await page.locator(".react-flow__pane").click();
  await page.keyboard.press("Control+z");
  await expect(page.getByTestId("node-greet")).toHaveCount(0);
});

test("Ask mode answers without changing anything", async ({ page }) => {
  await page.request.put("/api/v1/projects/prj_default/copilot-settings", {
    data: { source: "separate", model: "anthropic:claude-haiku-4-5" },
  });
  await page.goto("/");
  await page.getByLabel("workflow name").fill(`Copilot ask ${Date.now()}`);
  await page.getByRole("button", { name: "Create empty" }).click();
  await page.getByTestId("copilot-button").click();
  await page.getByRole("radio", { name: "Ask" }).click();
  await page.getByLabel("Message the Copilot").fill("What can you do?");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByTestId("copilot-panel")).toContainText("demo Copilot");
  await expect(page.getByTestId("copilot-proposal")).toHaveCount(0);
  // each answer says what it used; claude-haiku-4-5 has a catalog price, so in dollars too
  await expect(page.getByTestId("answer-cost").last()).toContainText(/\d+ tokens · \$\d/);
});

test("the Problems list hands its errors to the Copilot", async ({ page }) => {
  await page.request.put("/api/v1/projects/prj_default/copilot-settings", {
    data: { source: "separate", model: "anthropic:claude-haiku-4-5" },
  });
  await page.goto("/");
  await page.getByLabel("workflow name").fill(`Copilot fix ${Date.now()}`);
  await page.getByRole("button", { name: "Create empty" }).click();
  await page.locator('[data-testid="node-START"] .react-flow__handle').click();
  await page.getByTestId("port-add-core.llm").click(); // an Ask AI step without a model: a problem
  await page.getByTestId("ask-copilot-fix").click();
  await expect(page.getByTestId("copilot-panel")).toContainText("Find and fix the problems");
  await expect(page.getByTestId("copilot-panel")).toContainText("demo Copilot");
});

test("the person keeps part of a proposal: unticking a step drops its wires, and the canvas shows the rest", async ({ page }) => {
  await page.request.put("/api/v1/projects/prj_default/copilot-settings", { data: { source: "separate", model: "anthropic:claude-haiku-4-5" } });
  const wf = await (await page.request.post("/api/v1/projects/prj_default/workflows", { data: { name: `Partial ${Date.now()}` } })).json();
  await page.goto(`/workflows/${wf.id}`);
  await page.getByTestId("copilot-button").click();
  await page.getByLabel("Message the Copilot").fill("Build a workflow that greets people");
  await page.getByRole("button", { name: "Send" }).click();
  const card = page.getByTestId("copilot-proposal");
  await expect(card).toContainText("Add a Greet step");
  await expect(page.getByTestId("node-greet")).toBeVisible();

  await card.getByLabel("Add the step “Greet”").uncheck();
  await expect(card.getByLabel("Connect Start to “Greet”")).not.toBeChecked(); // its wires go with it
  await expect(page.getByTestId("node-greet")).toHaveCount(0); // and the preview no longer has it
  await card.getByRole("button", { name: "Accept the ticked changes" }).click();
  await expect(page.locator(".save-state")).toHaveText("Saved");
  const saved = await (await page.request.get(`/api/v1/workflows/${wf.id}`)).json();
  expect(saved.ir.nodes).toEqual([]);
  expect(saved.ir.resources.map((r: { name: string }) => r.name)).toEqual(["demo_model"]);
});

test("New chat keeps the conversation under History, and it can be reopened", async ({ page }) => {
  await page.request.put("/api/v1/projects/prj_default/copilot-settings", { data: { source: "separate", model: "anthropic:claude-haiku-4-5" } });
  const wf = await (await page.request.post("/api/v1/projects/prj_default/workflows", { data: { name: `History ${Date.now()}` } })).json();
  await page.goto(`/workflows/${wf.id}`);
  await page.getByTestId("copilot-button").click();
  const panel = page.getByTestId("copilot-panel");
  await page.getByLabel("Message the Copilot").fill("What can you do?");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(panel).toContainText("demo Copilot");
  await panel.getByRole("button", { name: "New chat" }).click();
  await expect(panel).toContainText("What are we building?");
  await page.getByTestId("copilot-history").click();
  await page.getByRole("menuitem", { name: /What can you do\?/ }).click();
  await expect(panel).toContainText("demo Copilot");
});

test("the inspector asks the Copilot about the selected block", async ({ page }) => {
  await page.request.put("/api/v1/projects/prj_default/copilot-settings", { data: { source: "separate", model: "anthropic:claude-haiku-4-5" } });
  const wf = await (await page.request.post("/api/v1/projects/prj_default/workflows", { data: { name: `Ask ${Date.now()}`, template: "ticket_triage" } })).json();
  await page.goto(`/workflows/${wf.id}`);
  await page.getByTestId("node-classify").click();
  await page.getByTestId("ask-copilot-block").getByRole("button", { name: /Ask the Copilot about this block/ }).click();
  await expect(page.getByTestId("copilot-panel")).toContainText("Explain the block");
  await expect(page.getByTestId("copilot-panel")).toContainText("demo Copilot");
});

test("a conversation can switch its model, and New chat goes back to the project's", async ({ page }) => {
  await page.request.put("/api/v1/projects/prj_default/copilot-settings", {
    data: { source: "separate", model: "anthropic:claude-haiku-4-5" },
  });
  const wf = await (await page.request.post("/api/v1/projects/prj_default/workflows", { data: { name: `Switch ${Date.now()}` } })).json();
  await page.goto(`/workflows/${wf.id}`);
  await page.getByTestId("copilot-button").click();
  const panel = page.getByTestId("copilot-panel");
  await expect(panel.getByTestId("copilot-model")).toContainText("Claude Haiku 4.5");
  // the header keeps History and New chat inside a narrow panel
  const box = (await panel.boundingBox())!;
  const newChat = (await panel.getByRole("button", { name: "New chat" }).boundingBox())!;
  expect(newChat.x + newChat.width).toBeLessThanOrEqual(box.x + box.width);
  await panel.getByTestId("copilot-model").click();
  await expect(page.getByTestId("copilot-model-setup")).toHaveAttribute("href", "/settings/models");
  await expect(page.getByRole("menuitemradio", { name: /fake/i })).toHaveCount(0); // the test-only model is not offered
  await page.getByRole("menuitemradio", { name: /^Claude Opus 5\.5/ }).click();
  await expect(panel.getByTestId("copilot-model")).toContainText("Claude Opus 5.5 · this chat");
  await page.getByLabel("Message the Copilot").fill("What can you do?");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(panel).toContainText("demo Copilot");
  await panel.getByRole("button", { name: "New chat" }).click();
  await expect(panel.getByTestId("copilot-model")).toContainText("Claude Haiku 4.5");
  await expect(panel.getByTestId("copilot-model")).not.toContainText("this chat");
});

test("the Copilot can write workspace code, saved only when accepted and credited to the person", async ({ page }) => {
  await page.request.put("/api/v1/projects/prj_default/copilot-settings", {
    data: { source: "separate", model: "anthropic:claude-haiku-4-5" },
  });
  await page.request.delete("/api/v1/projects/prj_default/files/scripts/demo_trim.py"); // from an earlier run
  const wf = await (await page.request.post("/api/v1/projects/prj_default/workflows", { data: { name: `Code ${Date.now()}` } })).json();
  await page.goto(`/workflows/${wf.id}`);
  await page.getByTestId("copilot-button").click();
  const panel = page.getByTestId("copilot-panel");
  await page.getByLabel("Message the Copilot").fill("Write a script that trims the text");
  await page.getByRole("button", { name: "Send" }).click();
  const files = panel.getByTestId("copilot-proposal-files");
  await expect(files).toContainText("scripts/demo_trim.py");
  await expect(files).toContainText("new");
  expect((await page.request.get("/api/v1/projects/prj_default/files/scripts/demo_trim.py")).status()).toBe(404);
  await files.getByText("Show the code").click();
  await expect(files).toContainText("def run(state: dict)");
  await panel.getByTestId("copilot-proposal").getByRole("button", { name: "Accept" }).click();
  await expect(panel.getByTestId("copilot-proposal")).toHaveCount(0);
  const saved = await (await page.request.get("/api/v1/projects/prj_default/files/scripts/demo_trim.py")).json();
  expect(saved.content).toContain("written by the demo Copilot");
});
