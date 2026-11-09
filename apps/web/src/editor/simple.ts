/** Simple mode (S31): the editor with only the blocks and panels a first workflow needs.
 *
 * It hides blocks for advanced patterns (subgraphs, maps, scripts, middleware, protocols, framework blocks), the
 * tests and code panels, and the canvas power tools. It hides blocks, not the product: Publish (Versions), Evaluate
 * and Workspace stay reachable. Nothing in the workflow changes: a block that is
 * already on the canvas stays, and switching back to Full shows everything again. The choice is remembered in this
 * browser and, when signed in, with the person (api/preferences.ts). */

/** The blocks Simple mode offers: steps that answer, decide, set values or ask a person, and their models and tools. */
export const SIMPLE_TYPES = new Set([
  "core.llm", "core.agent", "core.router", "core.set", "core.approval", "core.human_input", "knowledge.search",
  "model.chat", "tool.http", "tool.memory",
]);

/** Side panels and dock tabs Simple mode keeps. */
export const SIMPLE_TABS = new Set(["inspect", "copilot", "run", "versions"]);
export const SIMPLE_DOCK = new Set(["problems", "timeline", "runs"]);

const KEY = "agentcanvas.simple";

export function loadSimple(): boolean {
  try {
    return localStorage.getItem(KEY) === "1";
  } catch {
    return false;
  }
}

export function saveSimple(on: boolean): void {
  try {
    localStorage.setItem(KEY, on ? "1" : "0");
  } catch {
    // private windows and blocked storage: Simple mode just is not remembered
  }
}

/** The palette's blocks in Simple mode (all of them otherwise). */
export function paletteTypes<T extends { type: string }>(manifests: T[], simple: boolean): T[] {
  return simple ? manifests.filter((m) => SIMPLE_TYPES.has(m.type)) : manifests;
}
