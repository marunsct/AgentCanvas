import { describe, expect, it } from "vitest";
import { applyAll } from "../ir/commands";
import { triage } from "../test/fixtures";
import { copySelection, pasteCommands } from "./clipboard";

describe("copy/paste", () => {
  it("re-identifies nodes, keeps internal edges and makes names unique", () => {
    const wf = triage();
    const clip = copySelection(wf, ["n_ag", "r_smart", "r_send"], { n_ag: { x: 10, y: 20 } })!;
    expect(clip.edges.map((e) => e.id).sort()).toEqual(["w2", "w3"]);
    const { commands, positions, ids } = pasteCommands(wf, clip);
    const next = applyAll(wf, commands);
    expect(next.nodes.length).toBe(wf.nodes.length + 1);
    expect(next.resources.map((r) => r.name)).toContain("smart_model_2");
    const pasted = next.nodes.find((n) => ids.includes(n.id))!;
    expect(pasted.name).toBe("reply_drafter_2");
    expect(Object.values(positions)).toEqual([{ x: 50, y: 60 }]);
    expect(next.edges.filter((e) => ids.includes(e.target.node)).length).toBe(2);
  });
});
