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
    if (path.endsWith("/setup")) return { done: true, default_model: { model: "openai:gpt-4.1" } };
    return [];
  }),
}));
vi.mock("../run/sse", () => ({
  postStream: vi.fn(async (_path: string, body: Record<string, unknown>, on: (e: Record<string, unknown>) => void) => {
    sent.push(body);
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
});
