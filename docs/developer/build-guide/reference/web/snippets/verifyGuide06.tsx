import { Handle, Position, type IsValidConnection, type Node, type NodeProps } from "@xyflow/react";

export class ApiError extends Error {
  constructor(public status: number, public problem: { title?: string; detail?: string; errors?: unknown[] }) {
    super(problem.title ?? `HTTP ${status}`);
  }
}
export async function apiFetch<T>(path: string, init?: RequestInit & { version?: number }): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("content-type", "application/json");
  if (init?.version !== undefined) headers.set("If-Match", String(init.version));
  const res = await fetch(`/api/v1${path}`, { ...init, headers });
  if (!res.ok) throw new ApiError(res.status, await res.json().catch(() => ({})));
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

interface Port { name: string; type: string }
interface Manifest { icon: string; ports: { inputs: Port[] } }
interface WidgetData extends Record<string, unknown> { manifest: Manifest; label: string; status: string; routes?: string[] }
const PORT_COLORS: Record<string, string> = { ChatModel: "#8b5cf6" };

export function WidgetNode({ data }: NodeProps<Node<WidgetData>>) {
  const { manifest, label, status, routes } = data;
  return (
    <div className={`widget status-${status}`}>
      {manifest.ports.inputs.map((p, i) => (
        <Handle key={p.name} id={p.name} type="target" position={Position.Left}
                style={{ top: 24 + i * 16, background: PORT_COLORS[p.type] }} title={`${p.name}: ${p.type}`} />
      ))}
      <header>{manifest.icon} {label}</header>
      {(routes ?? ["next"]).map((r, i) => (
        <Handle key={r} id={routes ? `route:${r}` : "next"} type="source" position={Position.Right} style={{ top: 24 + i * 16 }} />
      ))}
    </div>
  );
}

export const makeIsValid = (portType: (nodeId: string, handle: string | null, dir: "in" | "out") => string | undefined)
  : IsValidConnection => (c) => {
    const out = portType(c.source, c.sourceHandle ?? null, "out");
    const inp = portType(c.target, c.targetHandle ?? null, "in");
    return !!out && !!inp && (out === inp || inp === "Any" || (inp === "Tool[]" && out === "Tool"));
  };
