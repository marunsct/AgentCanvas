import { expect, test } from "./fixtures";

// Wiring without a mouse: a port is a focusable button, Enter opens "what can connect here?", the menu takes the
// focus, Tab stays inside it, Escape gives the focus back to the port, and choosing a block adds it connected.

test("a block can be added and connected from the keyboard alone", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("workflow name").fill(`Keyboard ${Date.now()}`);
  await page.getByRole("button", { name: "Create empty" }).click();
  await expect(page.getByTestId("node-START")).toBeVisible();

  const port = page.locator('[data-testid="node-START"] .react-flow__handle');
  await port.focus();
  await page.keyboard.press("Enter");
  const menu = page.getByTestId("port-menu");
  await expect(menu).toBeVisible();
  await expect(menu).toContainText("What can come next?");

  // the menu took the focus, and Tab / Shift+Tab walk round inside it
  await expect.poll(() => menu.evaluate((el) => el.contains(document.activeElement))).toBe(true);
  for (let i = 0; i < 30; i++) await page.keyboard.press("Tab");
  expect(await menu.evaluate((el) => el.contains(document.activeElement))).toBe(true);
  await page.keyboard.press("Shift+Tab");
  expect(await menu.evaluate((el) => el.contains(document.activeElement))).toBe(true);

  // Escape closes it and the port has the focus again
  await page.keyboard.press("Escape");
  await expect(menu).toHaveCount(0);
  await expect(port).toBeFocused();

  // open again, pick a block with Enter: it appears already connected
  await page.keyboard.press("Enter");
  await expect(menu).toBeVisible();
  await page.getByTestId("port-add-core.llm").focus();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("node-ask_ai")).toBeVisible();
  await expect(page.locator(".react-flow__edge")).toHaveCount(1);
});
