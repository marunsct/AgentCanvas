// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { DeploymentOut, ShareOut } from "../api/generated";
import { ShareSection } from "./ShareSection";

const server: { share: ShareOut } = { share: {} as ShareOut };
vi.mock("../api/client", () => ({
  apiFetch: vi.fn(async (_path: string, init?: { method?: string; json?: Record<string, unknown> }) => {
    if (init?.method === "PUT") server.share = { ...server.share, ...init.json } as ShareOut;
    return server.share;
  }),
}));

const deployment = { id: "d1", environment: "prod" } as DeploymentOut;

afterEach(cleanup);

describe("ShareSection (S23)", () => {
  it("keeps unsaved typing when the switch changes the share", async () => {
    server.share = {
      deployment_id: "d1", enabled: false, greeting: "Hi", input: { field: "q", chat: true }, input_field: "q", inputs: ["q"],
      path: "/s/t", suggestions: [], title: "Saved title", token: "t", frame_origins: [],
    } as unknown as ShareOut;
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={qc}><ShareSection deployment={deployment} /></QueryClientProvider>);
    const title = await screen.findByLabelText("Title") as HTMLInputElement;
    expect(title.value).toBe("Saved title");
    fireEvent.change(title, { target: { value: "Typed, not saved" } });
    await act(async () => { fireEvent.click(screen.getByTestId("share-enabled")); });
    await waitFor(() => expect((screen.getByTestId("share-enabled") as HTMLInputElement).checked).toBe(true));
    expect(title.value).toBe("Typed, not saved");
  });

  it("saves the sites allowed to frame the chat page, one per line", async () => {
    server.share = {
      deployment_id: "d1", enabled: true, greeting: "", input: { field: "q", chat: false }, input_field: "q", inputs: ["q"],
      path: "/s/t", suggestions: [], title: "T", token: "t", frame_origins: [],
    } as unknown as ShareOut;
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={qc}><ShareSection deployment={deployment} /></QueryClientProvider>);
    const sites = await screen.findByTestId("share-sites") as HTMLTextAreaElement;
    expect(screen.getByText(/any website can put your chat page in a frame/)).toBeTruthy();
    fireEvent.change(sites, { target: { value: "https://a.example\n\n https://*.b.example " } });
    await act(async () => { fireEvent.click(screen.getByText("Save")); });
    await waitFor(() => expect(server.share.frame_origins).toEqual(["https://a.example", "https://*.b.example"]));
    expect(await screen.findByText(/Only these sites/)).toBeTruthy();
  });

  it("saves a reworded AI notice and never an empty one", async () => {
    server.share = {
      deployment_id: "d1", enabled: true, greeting: "", input: { field: "q", chat: false }, input_field: "q", inputs: ["q"],
      path: "/s/t", suggestions: [], title: "T", token: "t", frame_origins: [],
      ai_notice: "You are chatting with an AI assistant. Answers can be wrong.",
    } as unknown as ShareOut;
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={qc}><ShareSection deployment={deployment} /></QueryClientProvider>);
    const notice = await screen.findByLabelText("AI notice") as HTMLInputElement;
    expect(notice.value).toBe("You are chatting with an AI assistant. Answers can be wrong.");
    fireEvent.change(notice, { target: { value: "  " } });
    expect(screen.getByText(/cannot be empty/)).toBeTruthy();
    expect((screen.getByText("Save") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(notice, { target: { value: " Sie chatten mit einer KI. " } });
    await act(async () => { fireEvent.click(screen.getByText("Save")); });
    await waitFor(() => expect((server.share as { ai_notice?: string }).ai_notice).toBe("Sie chatten mit einer KI."));
  });

  it("follows a new default notice unless the builder is rewording it", async () => {
    const PUBLIC = "You are chatting with an AI assistant. Answers can be wrong.";
    const STAFF = "You are using an AI assistant. Answers can be wrong: check them before you rely on them.";
    server.share = {
      deployment_id: "d1", enabled: true, greeting: "", input: { field: "q", chat: false }, input_field: "q", inputs: ["q"],
      path: "/s/t", suggestions: [], title: "T", token: "t", frame_origins: [], ai_notice: PUBLIC,
    } as unknown as ShareOut;
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={qc}><ShareSection deployment={deployment} /></QueryClientProvider>);
    const notice = await screen.findByLabelText("AI notice") as HTMLInputElement;
    server.share = { ...server.share, ai_notice: STAFF } as ShareOut; // the system record now says: staff only
    await act(async () => { await qc.invalidateQueries({ queryKey: ["share", "d1"] }); });
    await waitFor(() => expect(notice.value).toBe(STAFF));
    fireEvent.change(notice, { target: { value: "Ein KI-Assistent." } });
    server.share = { ...server.share, ai_notice: PUBLIC } as ShareOut;
    await act(async () => { await qc.invalidateQueries({ queryKey: ["share", "d1"] }); });
    expect(notice.value).toBe("Ein KI-Assistent."); // what is being typed stays
  });

  it("picks the input messages go to, or leaves it to the server", async () => {
    server.share = {
      deployment_id: "d1", enabled: true, greeting: "", input: { field: "question", chat: false }, input_field: null,
      inputs: ["question", "messages"], path: "/s/t", suggestions: [], title: "T", token: "t", frame_origins: [],
    } as unknown as ShareOut;
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={qc}><ShareSection deployment={deployment} /></QueryClientProvider>);
    const select = await screen.findByLabelText("Messages go to") as HTMLSelectElement;
    expect(select.value).toBe(""); // picked by the server
    await act(async () => { fireEvent.change(select, { target: { value: "messages" } }); });
    await waitFor(() => expect(server.share.input_field).toBe("messages"));
    expect((screen.getByLabelText("Messages go to") as HTMLSelectElement).value).toBe("messages");
  });
});
