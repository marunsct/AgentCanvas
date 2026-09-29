/** Saved test cases (M6-T9): build one from the run on screen. Pure; unit-tested. */
import type { TestCaseIn } from "../api/generated";
import type { WF } from "../ir/model";
import type { RunState } from "../run/runStore";

type RunView = Pick<RunState, "input" | "finalOutput" | "route" | "interrupts" | "error" | "running">;

/** The test case "this run, as it just behaved": same input and debug aids; its status, route and outputs.
 * Chat-history channels are left out (message ids differ on every run). Null when the run cannot be replayed. */
export function testCaseFromRun(
  wf: WF, run: RunView, aids: { pins?: Record<string, unknown>; mocks?: Record<string, unknown[]> }, name: string,
): TestCaseIn | null {
  if (run.running || !run.input || (!run.finalOutput && !run.error)) return null;
  const status = run.error ? "error" : run.interrupts.length ? "interrupted" : "success";
  const chats = new Set(wf.state.channels.filter((c) => c.reducer === "add_messages").map((c) => c.name));
  const channels = Object.fromEntries(Object.entries(run.finalOutput ?? {}).filter(([k]) => !chats.has(k)));
  return {
    name, input: run.input,
    ...(aids.pins ? { pins: aids.pins as TestCaseIn["pins"] } : {}),
    ...(aids.mocks ? { mocks: aids.mocks } : {}),
    expect: { status, channels, route: run.route },
  };
}
