import { expect, test } from "./fixtures";

// Records the README's demo (docs/assets/demo.gif): pick a template, run it through a tool approval, read the
// generated LangGraph code, switch the framework and read that code too. Not part of the normal run: `make demo`
// (DEMO=1) records it against the fake-model server, so it shows made-up data and no real keys or people, and
// scripts/demo-gif.sh turns the video into the GIF.
test.beforeEach(() => {
  test.skip(!process.env.DEMO, "set DEMO=1 (make demo) to record the README's demo");
});

test.use({
  viewport: { width: 1600, height: 900 },
  colorScheme: "light",
  video: { mode: "on", size: { width: 1600, height: 900 } },
});

/** A visible pointer: recorded videos have none, and a viewer has to see where each click goes. */
const POINTER = () => {
  const show = () => {
    const dot = document.createElement("div");
    dot.id = "demo-pointer";
    Object.assign(dot.style, {
      position: "fixed", left: "0", top: "0", width: "18px", height: "18px", margin: "-9px 0 0 -9px",
      borderRadius: "50%", background: "rgba(220, 38, 38, 0.55)", border: "2px solid #fff",
      boxShadow: "0 0 0 1px rgba(0,0,0,.35)", pointerEvents: "none", zIndex: "2147483647",
      transition: "transform 0.25s ease-out",
    });
    document.body.appendChild(dot);
    document.addEventListener("mousemove", (e) => { dot.style.transform = `translate(${e.clientX}px, ${e.clientY}px)`; }, true);
    document.addEventListener("mousedown", () => { dot.style.background = "rgba(220, 38, 38, 0.9)"; }, true);
    document.addEventListener("mouseup", () => { dot.style.background = "rgba(220, 38, 38, 0.55)"; }, true);
  };
  if (document.body) show(); else document.addEventListener("DOMContentLoaded", show);
};

test("demo", async ({ page }) => {
  test.setTimeout(180_000);
  await page.addInitScript(POINTER);
  const pause = (ms: number) => page.waitForTimeout(ms);
  /** Glide to a spot first so the pointer travels instead of jumping. */
  const glide = async (locator: import("@playwright/test").Locator) => {
    const box = await locator.boundingBox();
    if (box) await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 15 });
    await pause(250);
  };

  await page.goto("/");
  const template = page.getByTestId("template-support_triage");
  await expect(template).toBeVisible();
  await pause(1200);
  await glide(template);
  await template.click();
  await expect(page.getByTestId("node-reply_drafter")).toBeVisible();
  await page.getByRole("button", { name: "fit view" }).click(); // as large as the canvas allows
  await pause(1800);

  // run it: the tool call waits for a person
  await glide(page.getByRole("tab", { name: "Run" }));
  await page.getByRole("tab", { name: "Run" }).click();
  await page.getByLabel("run input").fill('{"ticket": "Where is my invoice?"}');
  await pause(600);
  const run = page.getByRole("button", { name: "▶ Run" });
  await glide(run);
  await run.click();
  const approval = page.getByRole("group", { name: "Approval needed" });
  await expect(approval).toBeVisible({ timeout: 30_000 });
  await pause(1500);
  const approve = page.getByRole("button", { name: "Approve" });
  await glide(approve);
  await approve.click();
  await expect(page.getByTestId("run-output")).toBeVisible({ timeout: 30_000 });
  await pause(2000);

  // the code behind the picture, in LangGraph
  const code = page.getByRole("tab", { name: "Code" });
  await glide(code);
  await code.click();
  await expect(page.locator(".code-view")).toContainText("StateGraph");
  await pause(2500);

  // the same workflow on other frameworks
  for (const [fw, marker] of [["pydantic_ai", "from pydantic_ai import Agent"], ["agno", "from agno.agent import Agent"]]) {
    await page.locator(".react-flow__pane").click({ position: { x: 20, y: 20 } });
    await page.getByRole("tab", { name: "Inspector" }).click();
    const picker = page.getByLabel("framework", { exact: true });
    await glide(picker);
    await picker.selectOption(fw);
    await expect(page.locator(".save-state")).toHaveText("Saved");
    await page.getByRole("tab", { name: "Code" }).click();
    await expect(page.locator(".code-view")).toContainText(marker);
    await pause(2500);
  }
});
