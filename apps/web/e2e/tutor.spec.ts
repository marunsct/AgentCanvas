import { expect, test } from "./fixtures";

// Sprint 9 (FR-X-24): the guided build advances as the real workflow takes shape.

test("guided build starts from home and advances with the workflow", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("start-tour").click();
  await expect(page.getByTestId("tutor-step")).toHaveText("Give the workflow a memory");
  await expect(page.getByTestId("tutor")).toContainText("step 1 of 6");

  const id = page.url().split("/workflows/")[1];
  const cur = await (await page.request.get(`/api/v1/workflows/${id}`)).json();
  const ir = { ...cur.ir, state: { channels: [{ name: "question" }, { name: "answer" }] } };
  await page.request.put(`/api/v1/workflows/${id}`, { data: { ir }, headers: { "If-Match": String(cur.version) } });
  await page.reload();
  await expect(page.getByTestId("tutor-step")).toHaveText("Add a step that thinks");

  await page.getByRole("button", { name: "close the guided build" }).click();
  await expect(page.getByTestId("tutor")).toHaveCount(0);
  await page.reload();
  await expect(page.getByTestId("tutor")).toHaveCount(0); // closed for good
});
