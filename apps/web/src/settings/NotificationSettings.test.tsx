// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.hoisted(() => vi.fn());
vi.mock("../api/client", () => ({ ApiError: class extends Error {}, apiFetch }));

const stored = { email: { enabled: false, to: [], available: true }, slack: false, webhook_url: null, webhook_secret_set: false };
apiFetch.mockImplementation(async (path: string, init?: { method?: string; json?: Record<string, unknown> }) => {
  if (path === "/org/notifications/test") return [{ channel: "slack", ok: false, error: "hooks.slack.com answered HTTP 404" }];
  if (init?.method === "PUT") {
    const j = init.json ?? {};
    return { ...stored, slack: Boolean(j.slack_url), webhook_url: j.webhook_url ?? null, webhook_secret_set: Boolean(j.webhook_url),
      webhook_secret: j.webhook_url ? "whsec_shown_once" : null };
  }
  return stored;
});

const { NotificationSettings } = await import("./NotificationSettings");

afterEach(() => { cleanup(); apiFetch.mockClear(); });

function renderIt() {
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><NotificationSettings /></QueryClientProvider>);
}

describe("Settings › Organisation › Notifications", () => {
  it("sends only what changed and shows the webhook's secret once", async () => {
    renderIt();
    fireEvent.change(await screen.findByLabelText("Slack webhook address"), { target: { value: "https://hooks.slack.com/services/T/B/x" } });
    fireEvent.change(screen.getByLabelText("webhook address"), { target: { value: "https://example.com/hook" } });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Save" })); });
    expect(apiFetch).toHaveBeenCalledWith("/org/notifications", {
      method: "PUT", json: { slack_url: "https://hooks.slack.com/services/T/B/x", webhook_url: "https://example.com/hook" },
    });
    expect((await screen.findByTestId("webhook-secret")).textContent).toBe("whsec_shown_once");
    expect((screen.getByLabelText("Slack webhook address") as HTMLInputElement).value).toBe(""); // never shown back
  });

  it("sends a test and says how each channel went", async () => {
    renderIt();
    fireEvent.change(await screen.findByLabelText("Slack webhook address"), { target: { value: "https://hooks.slack.com/services/T/B/x" } });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Save" })); });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Send a test" })); });
    expect((await screen.findByLabelText("test results")).textContent).toContain("Slack: failed: hooks.slack.com answered HTTP 404");
  });
});
