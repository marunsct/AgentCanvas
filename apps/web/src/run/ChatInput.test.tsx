// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { normalize } from "../ir/model";

let stored: { type: string; content: string }[] = [];
vi.mock("../api/client", () => ({ ApiError: class extends Error {}, apiFetch: vi.fn(async () => ({ values: { messages: stored } })) }));
vi.mock("./session", () => ({ startRun: vi.fn(async () => {}), debugAids: () => ({}), cancelRun: vi.fn(), continueRun: vi.fn(), resumeRun: vi.fn() }));

const { ChatInput } = await import("./RunPanel");
const { useRun } = await import("./runStore");

afterEach(cleanup);

describe("chat input", () => {
  it("shows the message sent at once, before the run has stored it", async () => {
    stored = [];
    useRun.setState({ threadId: null, runSeq: 0, running: false, output: {} });
    const wf = normalize({ id: "wf_1", name: "w", state: { channels: [] } });
    render(<QueryClientProvider client={new QueryClient()}><ChatInput wf={wf} disabled={false} /></QueryClientProvider>);
    fireEvent.change(screen.getByLabelText("chat message"), { target: { value: "hello there" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(screen.getByTestId("chat-sent").textContent).toBe("hello there");
    // the run stored it: the conversation shows it once, from the thread
    stored = [{ type: "human", content: "hello there" }, { type: "ai", content: "hi" }];
    act(() => useRun.setState({ threadId: "t1", runSeq: 1 }));
    expect(await screen.findByText("hi")).toBeTruthy();
    expect(screen.queryByTestId("chat-sent")).toBeNull();
    expect(screen.getAllByText("hello there")).toHaveLength(1);
  });
});
