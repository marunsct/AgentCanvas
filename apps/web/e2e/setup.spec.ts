import { expect, test } from "./fixtures";

// First-run setup on a project of its own (the default project is shared with the other journeys): choose a
// provider, test the connection (the demo model answers on the key-less dev server), save, and templates use it.
test("first-run setup picks any provider's model and templates use it", async ({ page }) => {
  const project = await (await page.request.post("/api/v1/projects", { data: { name: `Setup ${Date.now()}` } })).json();
  await page.addInitScript((id) => localStorage.setItem("agentcanvas.project", id), project.id);
  await page.goto("/");
  await expect(page.getByTestId("setup-banner")).toBeVisible();
  await page.getByRole("link", { name: "Choose a model" }).click();

  await page.getByLabel("provider").selectOption("groq");
  await expect(page.getByTestId("setup-hint")).toContainText("GROQ_API_KEY");
  await expect(page.getByLabel("model", { exact: true })).toHaveValue("llama-3.3-70b-versatile");
  await page.getByLabel("API key").fill("gsk-test-key-123");
  await page.getByRole("button", { name: "Test connection" }).click();
  await expect(page.getByTestId("setup-test")).toContainText("Connected");

  await page.getByLabel("provider").selectOption("ollama");
  await expect(page.getByLabel("API key")).toHaveCount(0); // local models need no key
  await expect(page.getByLabel("base URL")).toHaveAttribute("placeholder", "http://localhost:11434");
  await page.getByLabel("model", { exact: true }).fill("qwen3");
  await page.getByRole("button", { name: "Save", exact: true }).click();

  await expect(page.getByTestId("default-model")).toContainText("ollama:qwen3");
  await page.getByTestId("template-chat_assistant").click();
  await page.getByTestId("node-chat_model").click();
  await expect(page.locator("input[id$='_model']")).toHaveValue("ollama:qwen3");
  await expect(page.getByTestId("model-provider-hint")).toContainText("Ollama");
});
