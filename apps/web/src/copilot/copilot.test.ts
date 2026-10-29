import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/client", () => ({ apiFetch: vi.fn() }));
vi.mock("../run/sse", () => ({ postStream: vi.fn() }));
import { apiFetch } from "../api/client";
import { postStream } from "../run/sse";
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

  it("Stop asks the server to end the turn, then closes the stream", async () => {
    let signal: AbortSignal | undefined;
    vi.mocked(postStream).mockImplementation((_path, _body, _onEvent, s) => {
      signal = s;
      return new Promise<void>((_resolve, reject) => s?.addEventListener("abort", () => reject(new Error("aborted"))));
    });
    vi.mocked(apiFetch).mockResolvedValue(conversation(null));
    await useCopilot.getState().load("wf_1");
    const sending = useCopilot.getState().send("what does it do?", "ask");
    await vi.waitFor(() => expect(signal).toBeDefined());
    vi.mocked(apiFetch).mockClear();
    vi.mocked(apiFetch).mockImplementation(async (path) => {
      expect(signal?.aborted).toBe(false); // the server is asked first: the answer is saved before the stream closes
      return (String(path).endsWith("/stop") ? { stopped: true } : conversation(null)) as never;
    });
    useCopilot.getState().stop();
    await sending;
    expect(vi.mocked(apiFetch).mock.calls[0]).toEqual(["/workflows/wf_1/copilot/stop", { method: "POST" }]);
    expect(signal?.aborted).toBe(true);
    expect(useCopilot.getState().busy).toBe(false);
  });

  it("names tools in plain words", () => {
    expect(toolLabel("propose_changes")).toBe("Proposed changes");
    expect(toolLabel("export_plan")).toBe("Checked what an export needs");
    expect(toolLabel("something_new")).toBe("something_new");
  });
});
