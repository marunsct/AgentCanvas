import type { APIRequestContext, Page } from "@playwright/test";
import { expect, test } from "./fixtures";

// The pages a person opens on a phone (PLAN §5): the Inbox, a share page and Runs fit a 390 x 844 screen, with
// nothing wider than the screen and nothing to scroll sideways.

test.use({ viewport: { width: 390, height: 844 } });

/** The page is no wider than the screen, and nothing sticks out of its right edge. */
async function fitsTheScreen(page: Page, what: string): Promise<void> {
  const widest = await page.evaluate(() => {
    const limit = document.documentElement.clientWidth;
    const out = [...document.querySelectorAll<HTMLElement>("body *")]
      .filter((el) => {
        const r = el.getBoundingClientRect();
        const style = getComputedStyle(el);
        // a bar or a code block that scrolls on its own (overflow auto or scroll) may be wider than the screen
        const scrolls = (e: Element | null) => { for (; e && e !== document.body; e = e.parentElement) { if (/auto|scroll/.test(getComputedStyle(e).overflowX)) return true; } return false; };
        return r.width > 0 && r.right > limit + 1 && style.position !== "fixed" && style.visibility !== "hidden"
          && !el.closest("[aria-hidden='true'], .visually-hidden, .toasts") && !scrolls(el);
      })
      .slice(0, 5)
      .map((el) => `${el.tagName.toLowerCase()}.${el.className} right=${Math.round(el.getBoundingClientRect().right)}`);
    return { scroll: document.documentElement.scrollWidth, limit, out };
  });
  expect(widest.scroll, `${what}: page is wider than the screen; sticking out: ${widest.out.join(", ")}`).toBeLessThanOrEqual(widest.limit);
  expect(widest.out, `${what}: sticks out past the right edge`).toEqual([]);
}

const GREETER = (name: string) => ({
  state: { channels: [{ name: "who" }, { name: "out" }] },
  nodes: [{ id: "n_g", type: "core.set", name: "greet", config: { values: { out: "Hi {{ state.who }}" } } }],
  edges: [
    { id: "e1", kind: "flow", source: { node: "START" }, target: { node: "n_g" } },
    { id: "e2", kind: "flow", source: { node: "n_g" }, target: { node: "END" } },
  ],
  name,
});

async function workflow(api: APIRequestContext, name: string, parts: Record<string, unknown>): Promise<{ id: string }> {
  const wf = await (await api.post("/api/v1/projects/prj_default/workflows", { data: { name } })).json();
  const put = await api.put(`/api/v1/workflows/${wf.id}`, { data: { ir: { ...wf.ir, ...parts } }, headers: { "If-Match": "1" } });
  expect(put.ok()).toBeTruthy();
  return wf;
}

test("the Inbox fits a phone, with a long step name and a long workflow name", async ({ page }) => {
  const long = `A workflow with a deliberately long name that would not fit in 390 pixels ${Date.now()}`;
  const wf = await workflow(page.request, long, {
    state: { channels: [{ name: "draft" }, { name: "approved" }] },
    nodes: [
      { id: "n_set", type: "core.set", name: "write", config: { values: { draft: "A long draft reply that somebody has to read before it is sent out to the customer, on a phone" } } },
      { id: "n_ok", type: "core.approval", name: "review_before_sending_the_reply_to_the_customer", config: { show: "draft", output: "approved" } },
    ],
    edges: [
      { id: "e1", kind: "flow", source: { node: "START" }, target: { node: "n_set" } },
      { id: "e2", kind: "flow", source: { node: "n_set" }, target: { node: "n_ok" } },
      { id: "e3", kind: "flow", source: { node: "n_ok" }, target: { node: "END" } },
    ],
  });
  const run = await page.request.post(`/api/v1/workflows/${wf.id}/runs/stream`, { data: { input: {} } });
  expect(run.ok()).toBeTruthy();
  await page.goto("/inbox");
  const item = page.getByTestId("inbox-item").filter({ hasText: long.slice(0, 30) });
  await expect(item.first()).toBeVisible({ timeout: 10_000 });
  // other tests' items may come first: bring this one up, then its button must be on screen (not off to the side)
  await item.first().scrollIntoViewIfNeeded();
  await expect(item.first().getByRole("button", { name: "Approve" })).toBeInViewport();
  await fitsTheScreen(page, "Inbox");
});

test("a share page fits a phone", async ({ page }) => {
  const wf = await workflow(page.request, `Phone share ${Date.now()}`, GREETER("x"));
  const pub = await (await page.request.post(`/api/v1/workflows/${wf.id}/publish`, { data: {} })).json();
  const share = await (await page.request.put(`/api/v1/deployments/${pub.deployment.id}/share`, {
    data: {
      enabled: true, title: "A greeting desk with a rather long title that wraps on a phone",
      greeting: "Tell me your name and I will greet you, in as many words as it takes to fill a few lines.",
      suggestions: ["Ada Lovelace, the first programmer", "Grace Hopper", "A suggestion that is long enough to need two lines on a phone"],
    },
  })).json();
  await page.goto(share.path);
  await expect(page.getByRole("heading", { name: /A greeting desk/ })).toBeVisible();
  await page.getByRole("button", { name: /Ada Lovelace/ }).click();
  await expect(page.getByTestId("share-assistant").last()).toContainText("Hi Ada Lovelace");
  await fitsTheScreen(page, "share page");
  await expect(page.getByLabel("Your message")).toBeInViewport(); // the box to type in stays on the screen
});

test("Runs fits a phone", async ({ page }) => {
  const wf = await workflow(page.request, `Phone runs ${Date.now()} with a long name`, GREETER("x"));
  for (const who of ["Ada", "Bo"]) {
    expect((await page.request.post(`/api/v1/workflows/${wf.id}/runs/stream`, { data: { input: { who } } })).ok()).toBeTruthy();
  }
  await page.goto("/runs");
  await expect(page.getByTestId("runs").locator("tbody tr").first()).toBeVisible();
  await fitsTheScreen(page, "Runs");
  await page.getByLabel("filter by workflow").selectOption(wf.id);
  await page.getByLabel(/^select conversation/).first().check();
  await expect(page.getByRole("region", { name: "add to a dataset" })).toBeVisible();
  await fitsTheScreen(page, "Runs with a selection");
});
