import { describe, expect, it } from "vitest";
import { frameworkBadge, FRAMEWORKS } from "./frameworks";

describe("framework badge", () => {
  it("names the framework of a wrapped agent", () => {
    expect(frameworkBadge("agent.framework", { framework: "crewai" })?.text).toBe("CrewAI");
    expect(frameworkBadge("agent.framework", { framework: "google_adk" })?.title).toContain("Google Agent Development Kit");
    expect(frameworkBadge("agent.framework", {})?.text).toBe("OpenAI Agents"); // the manifest default
    expect(frameworkBadge("agent.framework", { framework: "future_fw" })?.text).toBe("future_fw");
  });
  it("marks remote and MCP agents, and nothing else", () => {
    expect(frameworkBadge("agent.a2a", {})?.text).toBe("A2A");
    expect(frameworkBadge("agent.mcp", {})?.text).toBe("MCP");
    expect(frameworkBadge("core.agent", {})).toBeUndefined();
  });
  it("covers every framework the agent step offers", () => {
    expect(Object.keys(FRAMEWORKS).sort()).toEqual([
      "agno", "callable", "claude_agent_sdk", "crewai", "google_adk", "llamaindex", "microsoft_agent_framework",
      "openai_agents", "pydantic_ai", "strands",
    ]);
  });
});
