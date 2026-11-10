// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const connectors = [
  { id: "github", name: "GitHub", category: "Developer", description: "Repos.", config: { url: "https://x" }, local: false },
  { id: "slack", name: "Slack", category: "Productivity", description: "Chat.", config: { transport: "stdio" }, local: true },
  {
    id: "playwright", name: "Browser", category: "Web", description: "A browser.", config: { transport: "stdio" },
    local: true, sandboxable: true,
  },
];
vi.mock("../api/queries", () => ({
  useNodeTypes: () => ({ data: [], isLoading: false }),
  useWorkspaceCatalog: () => ({ data: undefined }),
  useMcpConnectors: () => ({ data: connectors }),
  useSetup: () => ({ data: undefined }),
}));
vi.mock("./Canvas", () => ({ addElement: vi.fn(), DND_MIME: "x", manifestCache: new Map(), projectModel: { current: null } }));

const { Palette } = await import("./Palette");

afterEach(cleanup);

describe("the connector gallery", () => {
  it("says which connectors start a program and which of those also run in the sandbox", () => {
    render(<Palette />);
    expect(screen.getByTestId("palette-connector-github").textContent).not.toContain("starts a program");
    expect(screen.getByTestId("palette-connector-slack").textContent).toContain("· starts a program");
    expect(screen.getByTestId("palette-connector-slack").textContent).not.toContain("sandbox");
    expect(screen.getByTestId("palette-connector-playwright").textContent).toContain("also in the sandbox");
  });
});
