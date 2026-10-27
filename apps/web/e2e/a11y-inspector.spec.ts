import { expect, test } from "./fixtures";

// Every control in the inspector can be reached with the keyboard and has a name a screen reader can read.

/** Controls without an accessible name: no label, aria-label, title or visible text (runs in the page). */
function nameless(root: Element): string[] {
  const controls = [...root.querySelectorAll<HTMLElement>("input, select, textarea, button, [role=button], [role=switch], [role=checkbox]")];
  return controls
    .filter((el) => !el.closest("[hidden]") && getComputedStyle(el).display !== "none" && (el as HTMLInputElement).type !== "hidden")
    .filter((el) => {
      const named = el.getAttribute("aria-label") || el.getAttribute("aria-labelledby") || el.getAttribute("title");
      const forLabel = el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      const text = (el.textContent ?? "").trim();
      return !(named || forLabel || el.closest("label") || (el.tagName === "BUTTON" && text));
    })
    .map((el) => el.outerHTML.slice(0, 140));
}

test("the inspector's controls have names and can be focused, for several kinds of block", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("template-support_triage").click();
  await expect(page.getByTestId("node-classify")).toBeVisible();
  const problems: string[] = [];
  let inspected = 0;
  for (const name of ["classify", "route", "draft"]) {
    const node = page.getByTestId(`node-${name}`);
    if (!(await node.count())) continue;
    await node.locator(".title").click();
    await page.getByRole("tab", { name: "Inspector" }).click();
    const panel = page.locator(".panel-body").first();
    await expect(panel).toBeVisible();
    const unnamed = await panel.evaluate(nameless);
    for (const n of unnamed) problems.push(`${name}: ${n}`);
    // every field can take the focus from the keyboard
    const fields = panel.locator("input:visible, select:visible, textarea:visible");
    const count = await fields.count();
    inspected += count;
    for (let i = 0; i < count; i++) {
      const tabindex = await fields.nth(i).getAttribute("tabindex");
      if (tabindex === "-1") problems.push(`${name}: field ${i} is taken out of the tab order`);
    }
  }
  expect(inspected, "the inspector showed fields to check").toBeGreaterThan(5);
  expect(problems, problems.join("\n")).toEqual([]);
});
