import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import { expect, test } from "./fixtures";

// Sprint 23: a published deployment is usable by a non-developer from a link (a chat page), and on any website
// through the chat widget.

test("publish, share as a chat page, chat as a signed-out visitor, embed the widget", async ({ page, browser, baseURL }) => {
  test.setTimeout(120_000);
  const api = page.request;
  const wf = await (await api.post("/api/v1/projects/prj_default/workflows", { data: { name: `Share ${Date.now()}` } })).json();
  const ir = {
    ...wf.ir, state: { channels: [{ name: "who" }, { name: "out" }] },
    nodes: [{ id: "n_g", type: "core.set", name: "greet", config: { values: { out: "Hi {{ state.who }}" } } }],
    edges: [
      { id: "e1", kind: "flow", source: { node: "START" }, target: { node: "n_g" } },
      { id: "e2", kind: "flow", source: { node: "n_g" }, target: { node: "END" } },
    ],
  };
  await api.put(`/api/v1/workflows/${wf.id}`, { data: { ir }, headers: { "If-Match": "1" } });
  await page.goto(`/workflows/${wf.id}`);
  await page.getByRole("tab", { name: "Versions" }).click();
  await page.getByRole("button", { name: "Publish to prod" }).click();
  await expect(page.getByTestId("deployment-prod")).toContainText("v1.0.0");

  // the builder turns sharing on and words the page
  const box = page.getByTestId("share-prod");
  await box.locator("summary").click();
  await box.getByLabel("Title").fill("Greeting desk");
  await box.getByLabel("Greeting", { exact: true }).fill("Tell me your name and I will greet you.");
  await box.getByLabel(/Suggested questions/).fill("Ada\nGrace");
  await box.getByRole("button", { name: "Save" }).click();
  await expect(box.getByRole("status")).toHaveText("Saved.");
  await box.getByTestId("share-enabled").click();
  await expect(box.getByTestId("share-enabled")).toBeChecked();
  const link = (await box.getByTestId("share-link").textContent())!.trim();
  expect(link).toMatch(/\/s\/sh_[A-Za-z0-9_-]+$/);
  const token = link.split("/s/")[1];
  await expect(box.getByTestId("share-embed")).toContainText(`data-share="${token}"`);

  // a visitor who has never signed in opens the link
  const visitor = await browser.newContext({ viewport: { width: 375, height: 720 } });
  const chat = await visitor.newPage();
  const shown = await chat.goto(link);
  if (process.env.CI || process.env.E2E_PROD) {
    expect(shown!.headers()["content-security-policy"]).toContain("frame-ancestors *");
    expect(shown!.headers()["x-frame-options"]).toBeUndefined();
  }
  await expect(chat.getByRole("heading", { name: "Greeting desk" })).toBeVisible();
  await expect(chat.getByText("Tell me your name and I will greet you.")).toBeVisible();
  await chat.getByRole("button", { name: "Ada" }).click();
  await expect(chat.getByTestId("share-assistant").last()).toContainText("Hi Ada");
  const box2 = chat.getByLabel("Your message");
  await box2.fill("Bo");
  await box2.press("Enter");
  await expect(chat.getByTestId("share-assistant").last()).toContainText("Hi Bo");
  await expect(chat.getByTestId("share-user")).toHaveCount(2);
  await chat.getByRole("button", { name: "Good answer" }).last().click();
  await expect(chat.getByRole("button", { name: "Good answer" }).last()).toHaveAttribute("aria-pressed", "true");
  // fits a phone: no sideways scrolling
  expect(await chat.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
  // G-15: a reload brings the visitor's conversation back, in the same thread
  await chat.reload();
  await expect(chat.getByTestId("share-user")).toHaveCount(2);
  await expect(chat.getByTestId("share-assistant").last()).toContainText("Hi Bo");
  await chat.getByRole("button", { name: "New conversation" }).click();
  await expect(chat.getByTestId("share-user")).toHaveCount(0);
  await chat.reload(); // started over for good: the old conversation does not come back
  await expect(chat.getByTestId("share-user")).toHaveCount(0);
  await expect(chat.getByRole("button", { name: "Grace" })).toBeVisible();
  await expect(chat.getByRole("button", { name: "Grace" })).toBeVisible();

  // another website adds the widget: a chat button opening the compact page in an iframe. The "website" is a real
  // server on another origin (127.0.0.1:<port> vs localhost:5173): Chromium holds back a page Playwright fulfils
  // itself from loading local addresses.
  const shop = createServer((_, res) => {
    res.setHeader("content-type", "text/html");
    res.end(`<!doctype html><html><body><h1>Shop</h1><script src="${baseURL}/widget.js" data-share="${token}" async></script></body></html>`);
  });
  await new Promise<void>((ok) => shop.listen(0, "127.0.0.1", ok));
  const site = await visitor.newPage();
  await site.goto(`http://127.0.0.1:${(shop.address() as AddressInfo).port}/`);
  // G-15: the shop's own script hears the widget's events (the page and the widget share one window)
  await site.evaluate(() => {
    const heard: string[] = [];
    (window as unknown as { heard: string[] }).heard = heard;
    window.addEventListener("message", (e) => { if (e.data?.agentcanvas === 1) heard.push(`${e.data.type}:${e.data.text ?? ""}`); });
  });
  await site.getByRole("button", { name: "Open AI assistant" }).click();
  const frame = site.frameLocator('iframe[title="AI assistant"]');
  await expect(frame.getByRole("heading", { name: "Greeting desk" })).toBeVisible();
  await expect(frame.getByTestId("share-page")).toHaveClass(/embed/);
  await expect(frame.getByTestId("share-ai-notice")).toContainText("AI assistant"); // AI Act Art. 50(1), in the widget too
  await frame.getByLabel("Your message").fill("Lin");
  await frame.getByRole("button", { name: "Send" }).click();
  await expect(frame.getByTestId("share-assistant").last()).toContainText("Hi Lin");
  await expect.poll(() => site.evaluate(() => (window as unknown as { heard: string[] }).heard)).toEqual(
    expect.arrayContaining(["opened:", "ready:", "reply:Hi Lin"]),
  );
  await site.getByRole("button", { name: "Close AI assistant" }).click();
  await expect(site.locator("iframe[title='AI assistant']")).toBeHidden();
  // ...and drives it: prefill opens the chat and puts the text in the message box
  await site.evaluate(() => window.postMessage({ agentcanvas: 1, type: "prefill", text: "Where is my order?" }, location.origin));
  await expect(site.locator("iframe[title='AI assistant']")).toBeVisible();
  await expect(frame.getByLabel("Your message")).toHaveValue("Where is my order?");
  await site.evaluate(() => window.postMessage({ agentcanvas: 1, type: "close" }, location.origin));
  await expect(site.locator("iframe[title='AI assistant']")).toBeHidden();

  // switching sharing off closes the page
  await box.getByTestId("share-enabled").click();
  await expect(box.getByTestId("share-enabled")).not.toBeChecked();
  await expect(box.getByTestId("share-link")).toHaveCount(0);
  await chat.reload();
  await expect(chat.getByRole("alert")).toContainText("does not work any more");
  await visitor.close();
  shop.close();
});
