import { describe, expect, it } from "vitest";
import type { Command } from "../ir/commands";
import { normalize } from "../ir/model";
import { dependencies, describe as say, preview, toggle } from "./review";

const base = normalize({
  id: "wf_1", name: "Base",
  nodes: [{ id: "n_old", type: "core.llm", name: "old", label: "Old step", config: {} }],
  edges: [{ id: "e_old", kind: "flow", source: { node: "START" }, target: { node: "n_old" } }],
} as never);

const cmds: Command[] = [
  { op: "AddResource", resource: { id: "r_m", type: "model.chat", name: "model", config: {} } },
  { op: "AddNode", node: { id: "n_greet", type: "core.llm", name: "greet", label: "Greet", config: {} } },
  { op: "Connect", edge: { id: "e_1", kind: "flow", source: { node: "START" }, target: { node: "n_greet" } } },
  { op: "Connect", edge: { id: "w_1", kind: "wiring", source: { node: "r_m" }, target: { node: "n_greet", port: "model" } } },
  { op: "RemoveNode", id: "n_old" },
] as Command[];

describe("reviewing a proposal", () => {
  it("says what each change does, naming steps the proposal itself adds", () => {
    const named = preview(base, cmds, new Set(cmds.keys())).workflow;
    expect(cmds.map((c) => say(c, named))).toEqual([
      "Add “model”", "Add the step “Greet”", "Connect Start to “Greet”", "Connect “model” to “Greet”", "Remove the step “Old step”",
    ]);
  });

  it("knows which changes need which, and keeps the ticks consistent", () => {
    const deps = dependencies(cmds);
    expect(deps).toEqual([[], [], [1], [0, 1], []]);
    const all = new Set([0, 1, 2, 3, 4]);
    const noStep = toggle(all, 1, deps); // unticking the step unticks the wires to it
    expect([...noStep].sort()).toEqual([0, 4]);
    const wire = toggle(new Set<number>(), 3, deps); // ticking a wire ticks what it connects
    expect([...wire].sort()).toEqual([0, 1, 3]);
  });

  it("previews the ticked changes, with removed blocks kept and marked", () => {
    const { workflow, diff } = preview(base, cmds, new Set([1, 2, 4]));
    expect(diff.added.sort()).toEqual(["e_1", "n_greet"]);
    expect(diff.removed.sort()).toEqual(["e_old", "n_old"]);
    expect(workflow.nodes.map((n) => n.id)).toEqual(["n_greet", "n_old"]); // the removed step is still drawn
    expect(preview(base, cmds, new Set()).diff).toEqual({ added: [], changed: [], removed: [] });
  });
});
