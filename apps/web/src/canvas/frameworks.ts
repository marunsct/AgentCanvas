/** The framework badge on agent steps from other frameworks (S16). Pure. */

/** Short badge text and the full name, per `agent.framework` framework id (kept in step with interop.FRAMEWORKS). */
export const FRAMEWORKS: Record<string, [string, string]> = {
  openai_agents: ["OpenAI Agents", "OpenAI Agents SDK"],
  crewai: ["CrewAI", "CrewAI"],
  google_adk: ["ADK", "Google Agent Development Kit"],
  pydantic_ai: ["Pydantic AI", "Pydantic AI"],
  agno: ["Agno", "Agno"],
  strands: ["Strands", "Strands Agents"],
  llamaindex: ["LlamaIndex", "LlamaIndex Workflows"],
  microsoft_agent_framework: ["MS Agent", "Microsoft Agent Framework"],
  claude_agent_sdk: ["Claude SDK", "Claude Agent SDK"],
  callable: ["Python", "your Python function"],
};

export interface FrameworkBadge {
  text: string;
  title: string;
}

/** The badge for a step, or undefined for ordinary steps (they run on LangGraph like the rest of the workflow). */
export function frameworkBadge(type: string, config: Record<string, unknown> | undefined): FrameworkBadge | undefined {
  if (type === "agent.framework") {
    const id = typeof config?.framework === "string" ? config.framework : "openai_agents";
    const [text, name] = FRAMEWORKS[id] ?? [id, id];
    return { text, title: `Built with ${name}; runs as one step of this workflow` };
  }
  if (type === "agent.a2a") return { text: "A2A", title: "A remote agent, called over the Agent2Agent protocol" };
  if (type === "agent.mcp") return { text: "MCP", title: "Runs one tool of a connected MCP server" };
  return undefined;
}
