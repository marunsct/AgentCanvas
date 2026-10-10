import { readFileSync } from "node:fs";
import { expect, test } from "./fixtures";

// S16: Open Agent Spec in and out, and the framework badge on agents from elsewhere.

const SAMPLE = readFileSync(new URL("../../api/tests/agentspec_sample.json", import.meta.url));

test("import an Agent Spec flow, read the notes, open it and see the remote agent badge", async ({ page }) => {
  await page.goto("/");
  await page.locator("#import-file").setInputFiles({ name: "support.json", mimeType: "application/json", buffer: SAMPLE });
  const notes = page.getByTestId("import-notes");
  await expect(notes).toContainText("Imported Support desk");
  await expect(notes).toContainText("works on the chat");
  await notes.getByRole("link", { name: "Open Support desk" }).click();
  await expect(page.getByTestId("node-classify_request")).toBeVisible();
  await expect(page.getByTestId("framework-billing_desk")).toHaveText("A2A");
});

test("download a workflow as Agent Spec; the file imports back exactly", async ({ page }) => {
  const api = page.request;
  const wf = await (await api.post("/api/v1/projects/prj_default/workflows", {
    data: { name: `Spec ${Date.now()}`, template: "classify_and_route" },
  })).json();
  await page.goto(`/workflows/${wf.id}`);
  await page.getByRole("tab", { name: "Versions" }).click();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: /Agent Spec file/ }).click();
  const file = await download;
  expect(file.suggestedFilename()).toMatch(/^spec-\d+\.agentspec\.json$/);
  await expect(page.getByTestId("agentspec-report")).toContainText("OpenAI-compatible");

  await page.goto("/");
  await page.locator("#import-file").setInputFiles(await file.path());
  await expect(page).toHaveURL(/\/workflows\/wf_/); // restored exactly: opens straight away
  await expect(page.getByTestId("node-route")).toBeVisible();
});
