// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const sent: unknown[] = [];
let security = {
  allowed_networks: ["10.0.0.0/8"], your_address: "10.1.2.3", networks_ignored: false,
  require_mfa: false, mfa_available: true, your_sign_in_is_mfa: true, private_projects: false,
};
vi.mock("../api/client", () => ({
  apiFetch: vi.fn(async (_path: string, init?: { method?: string; json?: { security: { allowed_networks: string[]; require_mfa?: boolean } } }) => {
    if (init?.method === "PUT" && init.json) {
      sent.push(init.json);
      security = { ...security, ...init.json.security };
    }
    return { name: "Acme", security };
  }),
}));
let admin = true;
vi.mock("../api/session", () => ({ useCan: () => admin, useCanOrg: () => admin }));
vi.mock("../ui/dialog", () => ({ toast: () => {} }));

const { OrgSecurity, networkLines } = await import("./OrgSecurity");

function show() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><OrgSecurity /></QueryClientProvider>);
}

afterEach(() => {
  cleanup(); sent.length = 0; admin = true;
  security = { ...security, allowed_networks: ["10.0.0.0/8"], require_mfa: false, your_sign_in_is_mfa: true, private_projects: false };
});

describe("OrgSecurity (E-8)", () => {
  it("reads one network per line", () => {
    expect(networkLines(" 10.0.0.0/8\n\n203.0.113.7 , 2001:db8::/32\n")).toEqual(["10.0.0.0/8", "203.0.113.7", "2001:db8::/32"]);
  });

  it("shows the list and the admin's own address, and saves the edited list", async () => {
    show();
    const box = await screen.findByTestId("allowed-networks") as HTMLTextAreaElement;
    expect(box.value).toBe("10.0.0.0/8");
    expect(screen.getByText("10.1.2.3")).toBeTruthy();
    fireEvent.change(box, { target: { value: "10.0.0.0/8\n192.0.2.0/24" } });
    await act(async () => { fireEvent.click(screen.getByText("Save")); });
    expect(sent).toEqual([{ security: { allowed_networks: ["10.0.0.0/8", "192.0.2.0/24"] } }]);
  });

  it("requires multi-factor sign-in, sending it only when it changed", async () => {
    show();
    const box = await screen.findByTestId("require-mfa") as HTMLInputElement;
    expect(box.checked).toBe(false);
    fireEvent.click(box);
    await act(async () => { fireEvent.click(screen.getByText("Save")); });
    expect(sent).toEqual([{ security: { allowed_networks: ["10.0.0.0/8"], require_mfa: true } }]);
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); }); // the saved settings come back
    await act(async () => { fireEvent.click(screen.getByText("Save")); });
    expect(sent[1]).toEqual({ security: { allowed_networks: ["10.0.0.0/8"] } });
  });

  it("cannot be switched on from a sign-in that is not multi-factor itself", async () => {
    security = { ...security, your_sign_in_is_mfa: false };
    show();
    expect(((await screen.findByTestId("require-mfa")) as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByText(/sign in with a second factor first/)).toBeTruthy();
  });

  it("makes projects private, sending it only when it changed (E-10)", async () => {
    show();
    const box = await screen.findByTestId("private-projects") as HTMLInputElement;
    expect(box.checked).toBe(false);
    expect(screen.getByText(/Every member sees every project/)).toBeTruthy();
    fireEvent.click(box);
    expect(screen.getByText(/Add each team to its projects first/)).toBeTruthy();
    await act(async () => { fireEvent.click(screen.getByText("Save")); });
    expect(sent).toEqual([{ security: { allowed_networks: ["10.0.0.0/8"], private_projects: true } }]);
  });

  it("is read-only for everyone but admins", async () => {
    admin = false;
    show();
    expect(((await screen.findByTestId("allowed-networks")) as HTMLTextAreaElement).disabled).toBe(true);
    expect(screen.queryByText("Save")).toBeNull();
  });
});
