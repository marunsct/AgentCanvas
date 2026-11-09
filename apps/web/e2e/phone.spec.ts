import { expect, test } from "./fixtures";

// W-8: on a phone the beginner guide and the guided build sit inside the screen, so they can be read and closed.

test.use({ viewport: { width: 390, height: 844 } });

test("on a phone the guide and the guided build fit the screen and close", async ({ page }) => {
  await page.addInitScript(() => { try { localStorage.removeItem("agentcanvas.helpSeen"); } catch { /* ignore */ } });
  const wf = await (await page.request.post("/api/v1/projects/prj_default/workflows", { data: { name: `Phone ${Date.now()}` } })).json();
  await page.goto(`/workflows/${wf.id}`);
  const guide = page.getByTestId("help-drawer");
  await expect(guide).toBeInViewport({ ratio: 1 }); // it sat 404 px from the right edge: off a 390 px screen
  await expect(page.getByLabel("close the guide")).toBeInViewport();
  await page.getByLabel("close the guide").click();
  await expect(guide).toHaveCount(0);

  await page.goto("/");
  await page.getByTestId("start-tour").click();
  const tutor = page.getByTestId("tutor");
  await expect(tutor).toBeInViewport({ ratio: 1 });
  await expect(page.getByLabel("close the guided build")).toBeInViewport();
});
