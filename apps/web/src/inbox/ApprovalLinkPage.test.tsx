// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { itemOf, type LinkInfo, tokenFromHash } from "./approvalLink";

const fetchMock = vi.fn();
const pending: LinkInfo = {
  interrupt_id: "int_1", action: "approve", kind: "tool_approval", status: "pending", workflow_name: "Support triage",
  project_name: "Default", step: "Reply", deciders: "lee@example.com", recipient: "lee@example.com",
  expires_at: "2026-11-17T10:00:00Z", decided_at: null, sign_in: false, why_sign_in: null, inbox_path: "/inbox?item=int_1",
};
// a token whose body names int_1 (the signature is the server's business)
const TOKEN = `${btoa(JSON.stringify({ v: 1, i: "int_1", a: "approve", u: "usr_lee", x: 1 })).replace(/=+$/, "")}.sig`;

function answer(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));
}

const { ApprovalLinkPage } = await import("./ApprovalLinkPage");

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  location.hash = `#${TOKEN}`;
});
afterEach(() => { cleanup(); fetchMock.mockReset(); vi.unstubAllGlobals(); });

const calls = () => fetchMock.mock.calls.map(([url, init]) => [url, init.method, JSON.parse(init.body)]);

describe("the page an Approve or Deny link opens", () => {
  it("reads the token from the fragment and the item from its body", () => {
    expect(tokenFromHash(`#${TOKEN}`)).toBe(TOKEN);
    expect(itemOf(TOKEN)).toBe("int_1");
    expect(itemOf("garbage")).toBeNull();
  });

  it("decides nothing when opened: only the button decides, once", async () => {
    fetchMock.mockImplementation((url: string) =>
      url.endsWith("/inspect") ? answer(200, pending) : answer(202, { run_id: "run_1", action: "approve" }));
    render(<ApprovalLinkPage />);
    expect(await screen.findByText("Approve this?")).toBeTruthy();
    expect(calls()).toEqual([["/api/v1/approval-links/inspect", "POST", { token: TOKEN }]]); // no decision yet
    expect(screen.getByText(/You decide as/).textContent).toContain("lee@example.com");
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Approve" })); });
    expect(calls()[1]).toEqual(["/api/v1/approval-links/decision", "POST", { token: TOKEN }]);
    expect((await screen.findByTestId("approve-done")).textContent).toContain("Approved");
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
  });

  it("asks a reason when denying a tool call", async () => {
    fetchMock.mockImplementation((url: string) =>
      url.endsWith("/inspect") ? answer(200, { ...pending, action: "deny" }) : answer(202, { run_id: "run_1", action: "deny" }));
    render(<ApprovalLinkPage />);
    fireEvent.change(await screen.findByLabelText("reason"), { target: { value: "wrong customer" } });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Deny" })); });
    expect(calls()[1][2]).toEqual({ token: TOKEN, reason: "wrong customer" });
    expect((await screen.findByTestId("approve-done")).textContent).toContain("Denied");
  });

  it("sends a channel's link to the Inbox, behind sign-in", async () => {
    fetchMock.mockImplementation(() => answer(200, { ...pending, recipient: null, sign_in: true,
      why_sign_in: "this link was posted to a channel, so whoever decides signs in first" }));
    render(<ApprovalLinkPage />);
    expect((await screen.findByTestId("approve-sign-in")).textContent).toContain("posted to a channel");
    expect(screen.getByRole("link", { name: "Open it in the Inbox" }).getAttribute("href")).toBe("/inbox?item=int_1");
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("says when the item was decided already, or the link no longer works", async () => {
    fetchMock.mockImplementationOnce(() => answer(200, { ...pending, status: "decided" }));
    render(<ApprovalLinkPage />);
    expect((await screen.findByTestId("approve-gone")).textContent).toContain("already decided");
    cleanup();
    fetchMock.mockImplementationOnce(() => answer(404, { detail: "this link has expired: open the item in the Inbox" }));
    render(<ApprovalLinkPage />);
    expect((await screen.findByRole("alert")).textContent).toContain("expired");
    expect(screen.getByRole("link", { name: "Open the Inbox" }).getAttribute("href")).toBe("/inbox?item=int_1");
  });
});
