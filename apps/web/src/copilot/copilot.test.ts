import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/client", () => ({ apiFetch: vi.fn() }));
import { apiFetch } from "../api/client";
import { toolLabel, useCopilot } from "./copilot";

const preview = { id: "wf_1", name: "w", nodes: [{ id: "n_greet", type: "core.llm", name: "greet" }], edges: [], resources: [] };
const conversation = (pending: unknown) => ({
  id: "cpc_1", enabled: true, messages: [], brief: null, proposals: [], pending, usage: {}, token_cap: 1000,
  model: { model: "anthropic:claude-haiku-4-5", origin: "Copilot settings" },
});

describe("Copilot store", () => {
  afterEach(() => vi.mocked(apiFetch).mockReset());

  it("previews an open proposal on the canvas, and not a stale or decided one", async () => {
    const diff = { added: ["n_greet"], changed: [], removed: [] };
    vi.mocked(apiFetch).mockResolvedValueOnce(conversation({ id: "p1", status: "pending", stale: false, diff, preview, summary: "s" }));
    await useCopilot.getState().load("wf_1");
    expect(useCopilot.getState().ghost?.diff.added).toEqual(["n_greet"]);
    expect(useCopilot.getState().ghost?.workflow.nodes[0].id).toBe("n_greet");

    vi.mocked(apiFetch).mockResolvedValueOnce(conversation({ id: "p1", status: "pending", stale: true, summary: "s" }));
    await useCopilot.getState().load("wf_1");
    expect(useCopilot.getState().ghost).toBeNull();
  });

  it("names tools in plain words", () => {
    expect(toolLabel("propose_changes")).toBe("Proposed changes");
    expect(toolLabel("something_new")).toBe("something_new");
  });
});
