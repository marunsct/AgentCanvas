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
      deployment_id: "d1", enabled: false, greeting: "Hi", input: { field: "q", chat: true }, input_field: "q",
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
      deployment_id: "d1", enabled: true, greeting: "", input: { field: "q", chat: false }, input_field: "q",
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
});
