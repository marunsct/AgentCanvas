import { expect, test } from "./fixtures";

// G-5: a person attaches an image to a file field in the Run tab, sees a preview, and the run gets the reference.

// a 1x1 PNG
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==", "base64");

test("attach an image to a file field in the Run tab and run with it", async ({ page }) => {
  const api = page.request;
  const wf = await (await api.post("/api/v1/projects/prj_default/workflows", { data: { name: `Files ${Date.now()}` } })).json();
  const ir = {
    ...wf.ir,
    state: { channels: [
      { name: "who" }, { name: "photo", type: { type: "object", format: "file", contentMediaType: "image/*" } }, { name: "out" },
    ] },
    nodes: [{ id: "n_g", type: "core.set", name: "greet", config: { values: { out: "{{ state.who }} sent {{ state.photo.name }} ({{ state.photo.media_type }})" } } }],
    edges: [
      { id: "e1", kind: "flow", source: { node: "START" }, target: { node: "n_g" } },
      { id: "e2", kind: "flow", source: { node: "n_g" }, target: { node: "END" } },
    ],
  };
  const put = await api.put(`/api/v1/workflows/${wf.id}`, { data: { ir }, headers: { "If-Match": "1" } });
  expect(put.ok(), await put.text()).toBeTruthy();
  await page.goto(`/workflows/${wf.id}`);
  await page.getByRole("tab", { name: "Run" }).click();

  // the sample input leaves the file out; the picker is below it
  await expect(page.getByLabel("run input")).not.toContainText("photo");
  await page.getByLabel("run input").fill(JSON.stringify({ who: "Ada" }));

  // something that is not an image is refused before it is sent
  await page.getByLabel("photo file").setInputFiles({ name: "notes.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.7") });
  await expect(page.getByRole("alert")).toContainText("not an image");

  // a page pretending to be a picture: the server reads its content and refuses it
  await page.getByLabel("photo file").setInputFiles({ name: "fake.png", mimeType: "image/png", buffer: Buffer.from("<script>alert(1)</script>") });
  await expect(page.getByRole("alert")).toContainText("image/*");

  await page.getByLabel("photo file").setInputFiles({ name: "me.png", mimeType: "image/png", buffer: PNG });
  await expect(page.getByAltText("Preview of me.png")).toBeVisible();
  await page.getByRole("button", { name: "▶ Run" }).click();
  await expect(page.getByTestId("run-output")).toContainText("Ada sent me.png (image/png)", { timeout: 15_000 });
  await expect(page.getByTestId("run-output")).not.toContainText("iVBOR"); // the state holds a reference, not the bytes
});
