// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/client", () => ({
  API_BASE: "/api/v1",
  apiFetch: vi.fn(async (path: string) =>
    path.endsWith("/triggers") ? [{ id: "tr1", kind: "cron", enabled: true, config: { schedule: "0 9 * * 1-5" } }] : []),
}));

const { TriggersSection } = await import("./TriggersSection");

afterEach(cleanup);

describe("TriggersSection (C-10)", () => {
  it("says that cron schedules are in UTC", async () => {
    render(<QueryClientProvider client={new QueryClient()}><TriggersSection wfId="wf_1" /></QueryClientProvider>);
    expect(screen.getByLabelText("cron schedule (UTC)")).toBeTruthy(); // nothing said which time zone 0 9 * * * meant
    expect((await screen.findByTestId("trigger-cron")).textContent).toContain("0 9 * * 1-5 UTC");
  });
});
