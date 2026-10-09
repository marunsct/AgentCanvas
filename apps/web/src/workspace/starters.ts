/** Starter code for new workspace files (S17): what each folder expects, so beginners start from working code. */

export type CodeKind = "scripts" | "tools" | "nodes" | "events" | "other";

export const KIND_LABEL: Record<CodeKind, string> = {
  scripts: "Script step",
  tools: "Tool for the AI",
  nodes: "Custom block",
  events: "Event handler",
  other: "Helper module",
};

/** The folder kind of a workspace path: `scripts/x.py` or `flows/<wf>/scripts/x.py` → "scripts". */
export function kindOf(path: string): CodeKind {
  const parts = path.replace(/^\/+/, "").split("/");
  const folder = parts[0] === "flows" && parts.length > 3 ? parts[2] : parts[0];
  return (["scripts", "tools", "nodes", "events"] as const).find((k) => k === folder) ?? "other";
}

const ident = (path: string) => (path.split("/").pop() ?? "x").replace(/\.py$/, "").replace(/[^a-z0-9_]/gi, "_").toLowerCase() || "x";

export function starter(path: string): string {
  if (!path.endsWith(".py")) return "";
  const name = ident(path);
  switch (kindOf(path)) {
    case "scripts":
      return `"""A Script step: gets the workflow data, returns the fields to change."""


def run(state: dict) -> dict:
    # read a field: state.get("text")
    text = str(state.get("text") or "")
    # return only the fields you want to update
    return {"text": text.strip()}
`;
    case "tools":
      return `"""A tool the AI can call; the docstring says when to use it."""
from langchain_core.tools import tool


@tool
def ${name}(query: str) -> str:
    """Describe what this tool does and when the AI should call it."""
    return f"result for {query}"
`;
    case "nodes":
      return `"""A custom block: it appears in the palette under its category."""
from agentcanvas.sdk import canvas_node


@canvas_node(
    name="custom.${name}", label="${name.replace(/_/g, " ")}", category="Custom", writes=["text"]
)
def ${name}(state: dict, cfg: dict) -> dict:
    return {"text": str(state.get("text") or "")}
`;
    case "events":
      return `"""An event handler: runs when such an event arrives."""
from agentcanvas.sdk import on


@on("example.created")
def ${name}(event, act):
    act.start_workflow("My workflow", input={"text": str(event.data)})
`;
    default:
      return `"""Helper code other files import."""\n`;
  }
}
