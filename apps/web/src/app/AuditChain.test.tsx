// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuditPage } from "./AuditPage";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { "content-type": "application/json" } });

describe("the audit log's integrity check", () => {
  it("checks the hash chain on request and lists what does not match", async () => {
    const asked: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      asked.push(String(url));
      if (String(url).includes("/org/audit/verify")) {
        return json({ ok: false, checked: 4, unchained: 0, head_hash: "ab", problems: [{ id: 12, kind: "changed", message: "this entry was changed after it was written" }] });
      }
      return json([]);
    }));
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuditPage /></QueryClientProvider>);
    expect(asked.some((u) => u.includes("/verify"))).toBe(false); // it reads the whole log: only when asked
    fireEvent.click(screen.getByRole("button", { name: "Check integrity" }));
    await waitFor(() => expect(screen.getByTestId("audit-chain").textContent).toContain("Not intact"));
    expect(screen.getByTestId("audit-chain").textContent).toContain("Entry 12: this entry was changed after it was written");
  });
});
