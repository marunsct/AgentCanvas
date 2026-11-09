// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/client", () => ({
  API_BASE: "/api/v1",
  apiFetch: vi.fn(async (path: string) =>
    path.endsWith("/triggers") ? [{ id: "tr1", kind: "cron", enabled: true, config: { schedule: "0 9 * * 1-5", timezone: "Europe/Berlin" },
      warnings: ["'github' signs in as the person who started the run, but a trigger's runs have no person"] }] : []),
}));

const { TriggersSection } = await import("./TriggersSection");

afterEach(cleanup);

describe("TriggersSection (C-10)", () => {
  it("says which time zone a schedule runs in, and sends the one chosen", async () => {
    render(<QueryClientProvider client={new QueryClient()}><TriggersSection wfId="wf_1" /></QueryClientProvider>);
    expect((await screen.findByTestId("trigger-cron")).textContent).toContain("0 9 * * 1-5 Europe/Berlin");
    const tz = screen.getByLabelText("cron time zone") as HTMLInputElement;
    expect(tz.value).toBe(Intl.DateTimeFormat().resolvedOptions().timeZone); // the person's own, not always UTC
    fireEvent.change(tz, { target: { value: "America/New_York" } });
    fireEvent.click(screen.getByRole("button", { name: "+ Cron" }));
    const { apiFetch } = await import("../api/client");
    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith("/workflows/wf_1/triggers", expect.objectContaining({
      method: "POST", json: expect.objectContaining({ config: expect.objectContaining({ timezone: "America/New_York" }) }),
    })));
  });

  it("shows what would stop a trigger's runs from working (N-5)", async () => {
    render(<QueryClientProvider client={new QueryClient()}><TriggersSection wfId="wf_1" /></QueryClientProvider>);
    expect((await screen.findByRole("note")).textContent).toContain("'github' signs in as the person who started the run");
  });
});
