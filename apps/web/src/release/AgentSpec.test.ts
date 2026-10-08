import { describe, expect, it } from "vitest";
import { specFileName } from "./AgentSpec";

describe("Agent Spec file name", () => {
  it("is a slug of the workflow name", () => {
    expect(specFileName("Ticket triage (v2)")).toBe("ticket-triage-v2.agentspec.json");
    expect(specFileName("  ")).toBe("workflow.agentspec.json");
  });
});
