import { expect, test } from "./fixtures";

// Sprint 23: the template gallery — filter by category, search, create from a template and land in the editor.

test("filter the gallery by category, search it, and open a template in the editor", async ({ page }) => {
  await page.goto("/");
  const gallery = page.getByTestId("template-gallery");
  await expect(gallery.getByTestId("template-count")).toContainText(/\d+ templates/);
  await expect(page.getByTestId("template-faq_bot")).toBeVisible();

  // category chips
  const chips = page.getByRole("group", { name: "Filter templates by category" });
  const engineering = chips.getByRole("button", { name: /^Engineering/ });
  await engineering.click();
  await expect(engineering).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("template-sql_helper")).toBeVisible();
  await expect(page.getByTestId("template-code_review")).toBeVisible();
  await expect(page.getByTestId("template-faq_bot")).toHaveCount(0);
  await expect(page.getByTestId("template-sql_helper")).toContainText("Engineering");
  await expect(page.getByTestId("template-sql_helper")).toContainText("blocks");
  await expect(page.getByTestId("template-sql_helper")).toContainText("beginner");

  // search narrows within the category; a search with no match offers a way back
  const search = page.getByLabel("search templates");
  await search.fill("diff");
  await expect(page.getByTestId("template-code_review")).toBeVisible();
  await expect(page.getByTestId("template-sql_helper")).toHaveCount(0);
  await search.fill("glossary");
  await expect(page.getByTestId("template-empty")).toContainText("in Engineering");
  await chips.getByRole("button", { name: /^All/ }).click();
  await expect(page.getByTestId("template-translation_glossary")).toBeVisible();
  await expect(page.getByTestId("template-count")).toContainText("1 of");
  await search.fill("no such template here");
  await page.getByRole("button", { name: "Show all templates" }).click();
  await expect(search).toHaveValue("");
  await expect(page.getByTestId("template-faq_bot")).toBeVisible();

  // keyboard: a card is a button; Enter creates the workflow and opens it in the editor
  await search.fill("faq");
  await page.getByTestId("template-faq_bot").focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/workflows\/wf_/);
  await expect(page.getByTestId("node-faq_agent")).toBeVisible();
  await expect(page.getByTestId("node-chat_model")).toBeVisible();
  await expect(page.locator(".problems")).not.toContainText("E0");
});

test("the gallery fits a phone screen", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.getByRole("group", { name: "Filter templates by category" }).getByRole("button", { name: /^HR/ }).click();
  const card = page.getByTestId("template-resume_screening");
  await expect(card).toBeVisible();
  const box = await card.boundingBox();
  expect(box && box.x >= 0 && box.x + box.width <= 390).toBeTruthy();
  const searchBox = await page.getByLabel("search templates").boundingBox();
  expect(searchBox && searchBox.x + searchBox.width <= 390).toBeTruthy();
});
