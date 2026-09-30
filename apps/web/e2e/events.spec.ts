import { expect, test } from "@playwright/test";

// Sprint 7 acceptance (J3 part 2): an event handler written as code starts a workflow; dry run first.

test("event map, dry run and delivery of a workspace event handler", async ({ page }) => {
  const stamp = Date.now();
  const wfName = `Greeter ${stamp}`;
  const type = `e2e${stamp}.ping`;
  const api = page.request;
  const wf = await (await api.post("/api/v1/projects/prj_default/workflows", { data: { name: wfName } })).json();
  const ir = {
    ...wf.ir, state: { channels: [{ name: "who" }, { name: "out" }] },
    nodes: [{ id: "n_g", type: "core.set", name: "greet", config: { values: { out: "Hi {{ state.who }}" } } }],
    edges: [
      { id: "e1", kind: "flow", source: { node: "START" }, target: { node: "n_g" } },
      { id: "e2", kind: "flow", source: { node: "n_g" }, target: { node: "END" } },
    ],
  };
  expect((await api.put(`/api/v1/workflows/${wf.id}`, { data: { ir }, headers: { "If-Match": "1" } })).ok()).toBeTruthy();
  const handler = [
    "from agentcanvas.sdk import on", "", "",
    `@on("${type}", filter="event.data.who != 'nobody'")`,
    "def greet(event, act):",
    `    act.start_workflow("${wfName}", input={"who": event.data["who"]})`, "",
  ].join("\n");
  expect((await api.put(`/api/v1/projects/prj_default/files/events/e2e_${stamp}.py`, { data: { content: handler } })).ok()).toBeTruthy();

  await page.goto("/events");
  const map = page.getByTestId("event-map");
  await expect(map).toContainText(type);
  await expect(map).toContainText(wfName);

  await page.getByLabel("event type").fill(type);
  await page.getByLabel("event data").fill('{"who": "Ada"}');
  await page.getByRole("button", { name: "Dry run" }).click();
  await expect(page.getByTestId("event-result")).toContainText("dry_run");
  await expect(page.getByTestId("event-result")).toContainText(`"workflow":"${wfName}"`);

  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByTestId("event-result")).toContainText("handled");
  await expect(page.getByTestId("event-result")).toContainText("started run_");
  await expect(page.getByTestId("event-log")).toContainText(type);
});
