import { describe, expect, it } from "vitest";
import { applyAll } from "../ir/commands";
import { manifests, triage } from "../test/fixtures";
import { linkEdge, suggest } from "../canvas/suggest";
import { DIAGNOSTIC_HELP, runErrorHelp } from "./diagnostics";
import { aType, CATEGORY_ORDER, firstSentence, portHelp } from "./glossary";

describe("beginner help", () => {
  it("every validation code the server can emit has a plain-English explanation and fix", () => {
    const sources = import.meta.glob<string>("../../../api/agentcanvas/validation/**/*.py", { query: "?raw", import: "default", eager: true });
    const codes = new Set(Object.values(sources).flatMap((src) => src.match(/"[EWI]\d{3}"/g) ?? []).map((c) => c.slice(1, -1)));
    expect(codes.size).toBeGreaterThan(30);
    for (const c of codes) expect(DIAGNOSTIC_HELP[c], c).toBeDefined();
  });

  it("explains common run failures", () => {
    expect(runErrorHelp("AuthenticationError: invalid x-api-key")?.what).toMatch(/API key/);
    expect(runErrorHelp("Connection refused: http://localhost:11434")?.fix).toMatch(/local model/);
    expect(runErrorHelp("something nobody has seen")).toBeUndefined();
  });

  it("every built-in block has a plain label, a known category and described ports", () => {
    for (const m of manifests.values()) {
      expect(CATEGORY_ORDER, m.type).toContain(m.category);
      expect(firstSentence(m.description).length, m.type).toBeGreaterThan(10);
      for (const p of [...(m.inputs ?? []), ...(m.outputs ?? [])]) expect(p.description, `${m.type}.${p.name}`).toBeTruthy();
    }
    expect(manifests.get("core.llm")?.label).toBe("Ask AI");
  });

  it("port hover text says what a port is for and what to do", () => {
    const llm = manifests.get("core.llm")!;
    const h = portHelp(llm, llm.inputs![0], "in");
    expect(h.title).toBe("model · AI model (required)");
    expect(h.action).toMatch(/Click/);
    expect(portHelp(undefined, undefined, "out", "route:urgent").title).toBe("Path “urgent”");
    expect(aType("ChatModel")).toBe("an AI model");
    expect(aType("Tool")).toBe("a tool");
  });
});

describe("what can connect here", () => {
  it("after a step: any step, Finish, and existing steps", () => {
    const wf = triage();
    const s = suggest(wf, manifests, { node: "n_esc", handle: "next", side: "out" })!;
    expect(s.add[0].type).toBe("core.llm"); // beginner favourites first
    expect(s.add.every((c) => manifests.get(c.type)?.kind === "runtime")).toBe(true);
    expect(s.existing.map((e) => e.id)).toContain("n_cls");
  });

  it("a route that already leads somewhere says so", () => {
    const s = suggest(triage(), manifests, { node: "n_rt", handle: "route:high", side: "out" })!;
    expect(s.add).toEqual([]);
    expect(s.title).toMatch(/already connected/);
  });

  it("a model input lists blocks that provide a model, and adding one wires it", () => {
    const wf = triage();
    wf.edges = wf.edges.filter((e) => !(e.kind === "wiring" && e.target.node === "n_cls"));
    const at = { node: "n_cls", handle: "model", side: "in" as const };
    const s = suggest(wf, manifests, at)!;
    expect(s.add.map((c) => c.type)).toEqual(["model.chat"]);
    expect(s.existing.length).toBeGreaterThan(0);
    const next = applyAll(wf, [
      { op: "AddResource", resource: { id: "r_new", type: "model.chat", name: "brain", config: {} } },
      { op: "Connect", edge: linkEdge(wf, at, "r_new", "model", "e_new") },
    ]);
    expect(next.edges.find((e) => e.id === "e_new")).toMatchObject({
      kind: "wiring", source: { node: "r_new", port: "model" }, target: { node: "n_cls", port: "model" },
    });
  });

  it("a model's output lists the blocks that take a model", () => {
    const s = suggest(triage(), manifests, { node: "r_fast", handle: "model", side: "out" })!;
    expect(s.add.map((c) => c.type)).toContain("core.agent");
    expect(s.add.find((c) => c.type === "mw.summarization")?.port).toBe("model");
  });
});
