import { answerDialog, expect, test } from "./fixtures";

// Sprint 24: Knowledge Studio — load the sample, search it by words and meaning, add a document of your own,
// look at its chunks, change the chunking and reindex.

test("load the sample knowledge base, search it, add a document and reindex", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("knowledge-link").click();
  await expect(page).toHaveURL(/\/knowledge$/);
  await expect(page.getByRole("heading", { name: "Knowledge", exact: true })).toBeVisible();

  const nav = page.getByRole("navigation", { name: "knowledge bases" });
  const sample = page.getByTestId("load-sample");
  await expect(sample.or(nav.getByRole("button", { name: /^handbook/ }))).toBeVisible();  // the list has loaded
  if (await sample.isVisible()) await sample.click();  // the database is shared by every e2e run
  await nav.getByRole("button", { name: /^handbook/ }).click();
  const kb = page.getByRole("region", { name: "knowledge base handbook" });
  await expect(kb.getByTestId("kb-counts")).toContainText("22 documents");

  // search: hybrid finds the vacation policy despite the typo; keyword mode is one click away
  await kb.getByLabel("search question").fill("how many vacaton days do I get");
  await kb.getByRole("button", { name: "Search" }).click();
  await expect(kb.getByTestId("kb-result").first()).toContainText("[1] Vacation policy");
  await kb.getByRole("radio", { name: "Keywords" }).click();
  await kb.getByLabel("search question").fill("expense reimbursement receipts");
  await kb.getByRole("button", { name: "Search" }).click();
  await expect(kb.getByTestId("kb-result").first()).toContainText("[1] Travel and expense reimbursement");

  // a knowledge base of your own: paste text, see its chunks, change the chunking, reindex
  const name = `notes-${Date.now().toString(36)}`;
  await page.getByLabel("new knowledge base name").fill(name);
  await page.getByRole("button", { name: "Create" }).click();
  const mine = page.getByRole("region", { name: `knowledge base ${name}` });
  await expect(mine.getByTestId("kb-counts")).toContainText("0 documents");
  await mine.getByLabel("text title").fill("Office plants");
  await mine.getByLabel("text", { exact: true }).fill("Water the ferns on Mondays.\n\nThe cactus needs water once a month.");
  await mine.getByRole("button", { name: "Add text" }).click();
  await expect(mine.getByTestId("kb-document")).toHaveCount(1);
  await mine.getByRole("button", { name: "Office plants", exact: true }).click();
  await expect(mine.getByTestId("kb-chunks")).toContainText("Water the ferns");

  await expect(mine.getByLabel("embedding model")).toHaveValue("builtin"); // only models that embed are offered
  await expect(mine.getByRole("link", { name: "Set up more models…" })).toHaveAttribute("href", "/settings/models");
  await mine.getByLabel("chunking strategy").selectOption("sentences");
  await expect(mine.getByTestId("kb-stale")).toBeVisible();
  await mine.getByRole("button", { name: "Reindex" }).click();
  await expect(mine.getByTestId("kb-stale")).toHaveCount(0);
  await mine.getByLabel("search question").fill("cactus");
  await mine.getByRole("button", { name: "Search" }).click();
  await expect(mine.getByTestId("kb-result").first()).toContainText("[1] Office plants");

  await mine.getByRole("button", { name: "Delete" }).click();
  await answerDialog(page);
  await expect(nav.getByRole("button", { name: new RegExp(`^${name}`) })).toHaveCount(0);
});

test("the Ask your documents template opens with its Search knowledge block", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("template-knowledge_qa").click();
  await expect(page).toHaveURL(/\/workflows\/wf_/);
  await expect(page.getByTestId("node-search")).toBeVisible();
  await expect(page.getByTestId("node-search")).toContainText("Search the handbook");
});
