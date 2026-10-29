// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const frameworks = [
  { id: "openai_agents", label: "OpenAI Agents SDK", packages: ["openai-agents"], api: "openai", structured_output: true, installed: true, own_env: false, hosted: false },
  { id: "claude_managed_agents", label: "Claude Managed Agents", packages: ["anthropic"], api: "anthropic", structured_output: false, installed: true, own_env: false, hosted: true },
];
const code = {
  framework: "claude_managed_agents", label: "Claude Managed Agents", code: "---\nname: helper\n---\n", file: "agents/helper/agent.md",
  problems: [], export_problems: [], gaps: [{ blocks: false, text: "lookup_order runs on your machine as a custom tool." }],
};
vi.mock("../api/client", () => ({
  API_BASE: "/api/v1",
  ApiError: class extends Error {},
  apiDownload: vi.fn(async () => undefined),
  apiFetch: vi.fn(async (path: string) => (path.endsWith("/code") ? code : frameworks)),
}));

const { DesignedAgent, EnginePicker } = await import("./Frameworks");
const { useEditor } = await import("./store");

afterEach(cleanup);
const node = { id: "n_a", type: "core.agent", name: "helper", engine: "claude_managed_agents", config: {} };
const mount = (ui: React.ReactElement) => render(<QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>);

describe("Claude Managed Agents in the Agent block's settings (G-12)", () => {
  it("is offered under agent platforms, apart from the frameworks that generate code", async () => {
    useEditor.setState({ wf: { framework: "langchain" } as never });
    mount(<EnginePicker node={node as never} />);
    const groups = await screen.findAllByRole("group");
    const platforms = groups.find((g) => g.getAttribute("label")?.startsWith("Agent platforms"));
    expect(platforms?.textContent).toBe("Claude Managed Agents");
    const generated = groups.find((g) => g.getAttribute("label")?.startsWith("Agent frameworks"));
    expect(generated?.textContent).toBe("OpenAI Agents SDK");
  });

  it("exports files for ant apply and lists what does not carry over", async () => {
    useEditor.setState({ workflowId: "wf_1", irHash: "h" });
    mount(<DesignedAgent node={node as never} />);
    expect((await screen.findByTestId("hosted-note")).textContent).toContain("ant apply");
    expect(screen.getByRole("button", { name: /Export for Claude Managed Agents \(ant apply\)/ })).toBeTruthy();
    expect((await screen.findByTestId("hosted-gaps")).textContent).toContain("custom tool");
    expect(screen.getByRole("button", { name: /agents\/helper\/agent.md file/ })).toBeTruthy();
  });
});
