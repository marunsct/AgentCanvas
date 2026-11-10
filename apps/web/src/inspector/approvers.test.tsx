// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import type { WidgetProps } from "@rjsf/utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import approval from "../../../api/agentcanvas/registry/nodes/core.approval.json";

vi.mock("../api/client", () => ({
  ApiError: class extends Error {},
  apiFetch: vi.fn(async () => [{ email: "lee@example.com" }, { email: "ops@example.com" }]),
}));

const { MemberEmailWidget } = await import("./widgets");
const { uiSchemaFromFormats } = await import("./schema");

afterEach(cleanup);

const widget = (value: string) => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const props = { id: "who", value, onChange: () => undefined } as unknown as WidgetProps;
  return render(<QueryClientProvider client={qc}><MemberEmailWidget {...props} /></QueryClientProvider>);
};

describe("Who may decide", () => {
  it("picks people from the organisation's members", async () => {
    const { container } = widget("Lee@Example.com");
    await waitFor(() => expect(container.querySelectorAll("datalist option")).toHaveLength(2));
    expect(screen.queryByText(/Not a member/)).toBeNull(); // case does not matter
  });

  it("marks an address no member has", async () => {
    widget("someone@else.com");
    expect(await screen.findByText(/Not a member of this organisation/)).toBeTruthy();
  });

  it("uses the member picker for the people of an approval step", () => {
    const ui = uiSchemaFromFormats(approval.config_schema as Record<string, unknown>);
    expect(ui).toMatchObject({ approvers: { people: { items: { "ui:widget": "member_email" } } } });
  });
});
