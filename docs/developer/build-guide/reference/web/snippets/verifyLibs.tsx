// Compile-only check for snippets used in build-guide/04 and 06.
import Form from "@rjsf/core";
import validator from "@rjsf/validator-ajv8";
import type { RJSFSchema } from "@rjsf/utils";
import ELK from "elkjs/lib/elk.bundled.js";
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import Editor from "@monaco-editor/react";
import { ReactFlow, addEdge, useEdgesState, useNodesState, type Connection, type Edge, type Node, type IsValidConnection } from "@xyflow/react";
import { useCallback } from "react";

const PORT_TYPES: Record<string, string> = { "n_llm:model": "ChatModel", "r_model:out": "ChatModel" };

export const isValid: IsValidConnection = (c) =>
  PORT_TYPES[`${c.source}:${c.sourceHandle}`] === PORT_TYPES[`${c.target}:${c.targetHandle}`];

const llmSchema: RJSFSchema = {
  type: "object", required: ["prompt", "output"],
  properties: { prompt: { type: "string", title: "Prompt" }, output: { type: "string", title: "Output channel" } },
};

export function Inspector({ config, onChange }: { config: Record<string, unknown>; onChange: (c: Record<string, unknown>) => void }) {
  return <Form schema={llmSchema} formData={config} validator={validator} onChange={(e) => onChange(e.formData as Record<string, unknown>)} />;
}

export async function layout(nodes: Node[], edges: Edge[]): Promise<Node[]> {
  const elk = new ELK();
  const res = await elk.layout({
    id: "root", layoutOptions: { "elk.algorithm": "layered", "elk.direction": "RIGHT" },
    children: nodes.map((n) => ({ id: n.id, width: 180, height: 60 })),
    edges: edges.map((e) => ({ id: e.id, sources: [e.source], targets: [e.target] })),
  });
  return nodes.map((n) => {
    const p = res.children?.find((c) => c.id === n.id);
    return { ...n, position: { x: p?.x ?? 0, y: p?.y ?? 0 } };
  });
}

export function Canvas() {
  const [nodes, , onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const onConnect = useCallback((c: Connection) => setEdges((es) => addEdge(c, es)), [setEdges]);
  const q = useQuery({ queryKey: ["wf"], queryFn: async () => (await fetch("/api/workflows/x")).json() });
  return (
    <>
      <ReactFlow nodes={nodes} edges={edges} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onConnect={onConnect} isValidConnection={isValid} />
      <Editor height="200px" defaultLanguage="python" value={q.data ? "loaded" : ""} />
    </>
  );
}
export const client = new QueryClient();
export const Provider = QueryClientProvider;
