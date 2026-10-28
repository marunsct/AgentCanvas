import type { Page } from "@playwright/test";
import { expect, SETTINGS, test } from "./fixtures";

// Sprint 17: a workspace per workflow with a way back, Copilot help with code, and every button labelled.

/** Visible buttons whose accessible name has no letter or digit (empty, or only a symbol such as "×" or "↶"). */
async function unnamedButtons(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const nameOf = (el: Element): string => {
      const by = el.getAttribute("aria-labelledby");
      if (by) return by.split(/\s+/).map((id) => document.getElementById(id)?.textContent ?? "").join(" ");
      return el.getAttribute("aria-label") || (el.textContent ?? "").trim() || el.getAttribute("title") || "";
    };
    return [...document.querySelectorAll("button, [role='button']")]
      .filter((el) => (el as HTMLElement).offsetParent !== null || (el as HTMLElement).getClientRects().length > 0)
      .filter((el) => !/[\p{L}\p{N}]/u.test(nameOf(el)))
      .map((el) => el.outerHTML.slice(0, 160));
  });
}

async function expectAllLabelled(page: Page, where: string) {
  await page.waitForLoadState("networkidle");
  expect(await unnamedButtons(page), `unlabelled buttons on ${where}`).toEqual([]);
}

async function newWorkflow(page: Page, name: string): Promise<string> {
  await page.goto("/");
  await page.getByLabel("workflow name").fill(name);
  await page.getByRole("button", { name: "Create empty" }).click();
  await expect(page.locator(".save-state")).toHaveText("Saved");
  return page.url();
}

test("a workflow's own code: starter file, Copilot help, and the way back to the same block", async ({ page }) => {
  await page.request.put("/api/v1/projects/prj_default/copilot-settings", {
    data: { source: "separate", model: "anthropic:claude-haiku-4-5" },
  });
  const stamp = Date.now();
  const editorUrl = await newWorkflow(page, `Own code ${stamp}`);
  const wfId = editorUrl.split("/workflows/")[1];
  await page.getByTestId("palette-core.script").dragTo(page.locator(".react-flow__pane"), { targetPosition: { x: 330, y: 180 } });
  await page.getByLabel("node name").fill(`count_${stamp}`);
  await page.getByLabel("node name").blur();
  await page.getByRole("button", { name: `+ Create the code file count_${stamp}.py` }).click();
  await expect(page.locator("input[id$='_import_path']")).toHaveValue(`flows.${wfId.toLowerCase()}.scripts.count_${stamp}:run`);

  // the file lives in this workflow's own folder, next to the shared code
  await page.getByTestId("open-script").click();
  await expect(page).toHaveURL(new RegExp(`/workflows/${wfId}/workspace`));
  await expect(page.getByRole("list", { name: "this workflow's files" })).toContainText(`scripts/count_${stamp}.py`);
  await expect(page.getByRole("list", { name: "shared files" })).toContainText("scripts/example.py");
  await expect(page.getByTestId("code-editor")).toContainText("def run(state: dict) -> dict:");

  // the Copilot proposes code as a diff; accepting puts it in the editor, unsaved
  await page.getByLabel("what the code should do").fill("also count the words in text");
  await page.getByRole("button", { name: "Write with Copilot" }).click();
  await expect(page.getByTestId("code-diff")).toContainText("+ def count_words(text: str) -> int:");
  await page.getByRole("button", { name: "Accept changes" }).click();
  await expect(page.getByTestId("code-editor")).toContainText("def count_words");
  await expect(page.getByText("unsaved")).toBeVisible();
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByTestId("lint")).toContainText("ruff: no problems");
  await page.getByRole("button", { name: "Explain" }).click();
  await expect(page.getByTestId("code-answer")).toContainText("lines of Python");
  await expectAllLabelled(page, "the workflow's Workspace");

  // back to the canvas, with the same block selected
  await page.getByTestId("back-to-workflow").click();
  await expect(page).toHaveURL(editorUrl);
  await expect(page.getByLabel("node name")).toHaveValue(`count_${stamp}`);
  // the browser's Back button works too
  await page.getByTestId("more-menu").click();
  await page.getByTestId("workspace-link").click();
  await expect(page.getByTestId("back-to-workflow")).toContainText(`Own code ${stamp}`);
  await page.goBack();
  await expect(page).toHaveURL(editorUrl);
  await expect(page.getByLabel("node name")).toHaveValue(`count_${stamp}`);
});

test("list fields in the AI agent block have labelled buttons and start empty", async ({ page }) => {
  await newWorkflow(page, `Agent ${Date.now()}`);
  await page.getByTestId("palette-core.agent").dragTo(page.locator(".react-flow__pane"), { targetPosition: { x: 330, y: 180 } });
  const add = page.getByRole("button", { name: "Add an item" }).first();
  await expect(add).toHaveText("+ Add");
  await add.click();
  await expect(page.getByRole("button", { name: "Remove this item" })).toBeVisible();
  await expect(page.locator(".rjsf")).not.toContainText('Instance type "null" is invalid');
  await expectAllLabelled(page, "the AI agent block");
});

test("no page has an unlabelled button", async ({ page }) => {
  const editorUrl = await newWorkflow(page, `Labels ${Date.now()}`);
  for (const tab of ["Inspector", "Copilot", "Run", "Tests", "Versions", "Code"]) {
    await page.getByRole("tab", { name: tab, exact: true }).click();
    await expectAllLabelled(page, `the editor's ${tab} tab`);
  }
  for (const dock of ["Timeline", "Explain", "Problems"]) {
    await page.getByRole("tab", { name: dock, exact: true }).click();
    await expectAllLabelled(page, `the editor's ${dock} panel`);
  }
  for (const path of ["/", "/workspace", "/inbox", "/memory", "/events", "/playground", "/knowledge", "/runs", ...SETTINGS,
    `${new URL(editorUrl).pathname}/evaluation`, `${new URL(editorUrl).pathname}/workspace`]) {
    await page.goto(path);
    await expectAllLabelled(page, path);
  }
});
