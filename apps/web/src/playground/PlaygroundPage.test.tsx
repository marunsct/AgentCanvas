// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { takesTemperature } from "./playground";

const models = [
  { id: "openai:gpt-4.1", label: "GPT-4.1", provider: "openai" },
  { id: "anthropic:claude-haiku-5-5", label: "Claude Haiku 5.5", provider: "anthropic", sampling: false },
];
const sent: Record<string, unknown>[] = [];
vi.mock("../api/client", () => ({
  apiFetch: vi.fn(async (path: string) => {
    if (path === "/models") return models;
    if (path.includes("/workspace/catalog")) {
      return { tools: [{ name: "weather", import_path: "tools.weather:weather", description: "The weather in a city.", workflow: null }] };
    }
    if (path.endsWith("/setup")) return { done: true, default_model: { model: "openai:gpt-4.1" } };
    return [];
  }),
}));
vi.mock("../run/sse", () => ({
  postStream: vi.fn(async (_path: string, body: Record<string, unknown>, on: (e: Record<string, unknown>) => void) => {
    sent.push(body);
    if ((body.tools as string[]).length) on({ type: "tool", name: "weather", args: { city: "Paris" }, result: "sunny in Paris" });
    on({ type: "delta", text: "hi" });
  }),
}));

const { PlaygroundPage } = await import("./PlaygroundPage");

afterEach(() => { cleanup(); sent.length = 0; sessionStorage.clear(); });

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><MemoryRouter><PlaygroundPage /></MemoryRouter></QueryClientProvider>);
}

async function say(text: string) {
  fireEvent.change(screen.getByLabelText("Your message"), { target: { value: text } });
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Send" })); });
}

describe("takesTemperature", () => {
  it("is false only for a model the catalog marks sampling: false", () => {
    expect(takesTemperature(models, "anthropic:claude-haiku-5-5")).toBe(false);
    expect(takesTemperature(models, "openai:gpt-4.1")).toBe(true);
    expect(takesTemperature(models, "ollama:llama3")).toBe(true); // not in the catalog: may take one
    expect(takesTemperature(undefined, "openai:gpt-4.1")).toBe(true);
  });
});

describe("PlaygroundPage temperature", () => {
  it("sends the temperature chosen, and none until one is chosen", async () => {
    renderPage();
    await screen.findByRole("option", { name: /GPT-4.1 \(openai:gpt-4.1\)/ });
    await say("one");
    expect(sent[0].temperature).toBeUndefined();
    fireEvent.change(screen.getByLabelText("Temperature"), { target: { value: "0.3" } });
    expect(screen.getByTestId("pg-temperature-value").textContent).toBe("0.3");
    await say("two");
    expect(sent[1].temperature).toBe(0.3);
  });

  it("is off for a model that takes no temperature, and sends none", async () => {
    renderPage();
    await screen.findByRole("option", { name: /Claude Haiku 5.5/ });
    fireEvent.change(screen.getByLabelText("Temperature"), { target: { value: "0.3" } });
    fireEvent.change(screen.getByLabelText("Model"), { target: { value: "anthropic:claude-haiku-5-5" } });
    expect((screen.getByLabelText("Temperature") as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByText(/This model takes no temperature/)).toBeTruthy();
    await say("hello");
    expect(sent[0].temperature).toBeUndefined();
  });

  it("sends the workspace tools picked and shows what the model called", async () => {
    renderPage();
    await say("no tools yet");
    expect(sent[0].tools).toEqual([]);
    fireEvent.click(await screen.findByLabelText(/weather/));
    await say("weather in Paris?");
    expect(sent[1].tools).toEqual(["tools.weather:weather"]);
    expect(await screen.findByText("Called")).toBeTruthy();
    expect(screen.getByTestId("pg-calls").textContent).toContain("sunny in Paris");
  });
});

describe("PlaygroundPage stream", () => {
  it("stops the answer being written when the page is left", async () => {
    const { postStream } = await import("../run/sse");
    let signal: AbortSignal | undefined;
    vi.mocked(postStream).mockImplementationOnce((_p, _b, _on, s) => {
      signal = s;
      return new Promise((_ok, fail) => s?.addEventListener("abort", () => fail(new DOMException("aborted", "AbortError"))));
    });
    renderPage();
    await say("write a long story");
    expect(signal?.aborted).toBe(false);
    cleanup(); // moved to another page
    expect(signal?.aborted).toBe(true);
  });
});

describe("PlaygroundPage conversation kept in the tab", () => {
  it("is shown again to the same person, never to another person or in another organisation", async () => {
    const { useMe } = await import("../api/session");
    const before = useMe.getState().me;
    try {
      useMe.setState({ me: { ...before, id: "usr_a", org_id: "org_1" } });
      renderPage();
      await say("customer 4411 asked for a refund");
      expect(screen.getByText("customer 4411 asked for a refund")).toBeTruthy();
      cleanup();
      renderPage(); // moving to another page and back keeps it
      expect(screen.getByText("customer 4411 asked for a refund")).toBeTruthy();
      cleanup();
      useMe.setState({ me: { ...before, id: "usr_b", org_id: "org_1" } });
      renderPage();
      expect(screen.queryByText("customer 4411 asked for a refund")).toBeNull();
      cleanup();
      useMe.setState({ me: { ...before, id: "usr_a", org_id: "org_2" } });
      renderPage();
      expect(screen.queryByText("customer 4411 asked for a refund")).toBeNull();
      expect(Object.values(sessionStorage).join(" ")).not.toContain("4411"); // dropped, not just hidden
    } finally {
      useMe.setState({ me: before });
    }
  });

  it("drops a conversation kept under the old, unscoped key", () => {
    sessionStorage.setItem("agentcanvas.playground", JSON.stringify([{ role: "user", content: "from before" }]));
    renderPage();
    expect(screen.queryByText("from before")).toBeNull();
    expect(sessionStorage.getItem("agentcanvas.playground")).toBeNull();
  });
});
