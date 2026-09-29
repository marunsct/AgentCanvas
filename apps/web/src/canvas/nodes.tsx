/** Manifest-driven node renderers (M2-T5). One generic widget draws every node type from its manifest. */
import { Handle, type Node, type NodeProps, Position, useUpdateNodeInternals } from "@xyflow/react";
import { memo, useEffect, useRef } from "react";
import { type CanvasData, PORT_COLORS } from "./irToFlow";

const ICONS: Record<string, string> = {
  sparkles: "✨", bot: "🤖", split: "🔀", "user-check": "🙋", pencil: "✏️", code: "⌨️", cpu: "🔌", wrench: "🔧", box: "▪",
  brain: "🧠", shield: "🛡", gauge: "⏱", "rotate-cw": "🔁", "life-buoy": "🛟", scroll: "📜", "list-checks": "☑",
  filter: "🔎", file: "📄", database: "🗄", "message-square": "💬", "git-fork": "🔱", merge: "⛙", plug: "🔌", globe: "🌐",
};
const STATUS_LABEL: Record<string, string> = { running: "running", ok: "done", error: "error", interrupted: "waiting" };

function Badges({ data }: { data: CanvasData }) {
  return (
    <span className="badges">
      {data.status !== "idle" && <span className={`badge status-${data.status}`}>{STATUS_LABEL[data.status]}</span>}
      {data.errors > 0 && <span className="badge err" title={`${data.errors} error(s)`}>⛔ {data.errors}</span>}
      {data.warnings > 0 && <span className="badge warn" title={`${data.warnings} warning(s)`}>⚠ {data.warnings}</span>}
    </span>
  );
}

const ROW = 22;
const HEAD = 40;

/** React Flow caches handle positions; re-measure when a node's set of handles changes (new route, ports loaded). */
function useHandleSync(id: string, signature: string) {
  const update = useUpdateNodeInternals();
  const first = useRef(signature);
  useEffect(() => {
    // the initial measurement is done by React Flow itself; re-measuring every node on mount is O(n²)
    if (first.current === signature) return;
    first.current = signature;
    update(id);
  }, [id, signature, update]);
}

export const WidgetNode = memo(function WidgetNode({ id, data, selected }: NodeProps<Node<CanvasData>>) {
  const m = data.manifest;
  const wiring = m?.inputs ?? [];
  const flowOuts = (m?.outputs ?? []).filter((p) => p.type === "Flow").map((p) => p.name);
  const outs = data.routes ? data.routes.map((r) => `route:${r}`) : flowOuts.length ? flowOuts : ["next"];
  useHandleSync(id, [...wiring.map((p) => p.name), ...outs].join("|"));
  const rows = Math.max(wiring.length, outs.length, 1);
  return (
    <div
      className={`widget status-${data.status}${selected ? " selected" : ""}${data.disabled ? " disabled" : ""}${data.highlight ? " highlight" : ""}`}
      style={{ borderTopColor: m?.color ?? "#64748b", minHeight: HEAD + rows * ROW + 8 + (data.preview ? 34 : 0) }}
      data-testid={`node-${data.name}`}
    >
      <Handle type="target" id="in" position={Position.Left} className="h-flow" style={{ top: 20 }} title="flow in" />
      <header>
        <span className="icon">{ICONS[m?.icon ?? "box"] ?? "▪"}</span>
        <span className="title" title={data.type}>{data.label}</span>
        <Badges data={data} />
      </header>
      {data.breakpoint && <span className="bp-dot" title="breakpoint: debug runs pause before this node" />}
      {data.pinned && <span className="pin" title="pinned output: debug runs replay it" data-testid={`pinned-${data.name}`}>📌</span>}
      <div className="subtitle">
        {data.name}{data.disabled ? " · bypassed" : ""}
        {data.metrics && <span className="metrics" data-testid={`metrics-${data.name}`}> · {data.metrics}</span>}
      </div>
      {wiring.map((p, i) => (
        <div key={p.name} className="port-row in" style={{ top: HEAD + i * ROW }}>
          <Handle
            type="target" id={p.name} position={Position.Left} className="h-port"
            style={{ top: 9, background: PORT_COLORS[p.type] ?? "#94a3b8" }} title={`${p.name}: ${p.type}${p.multi ? "[]" : ""}`}
          />
          <span>{p.name}{p.required ? "*" : ""}</span>
          <em>{p.type}{p.multi ? "[]" : ""}</em>
        </div>
      ))}
      {data.preview && <div className="preview" style={{ top: HEAD + rows * ROW }} title={data.preview}>{data.preview}</div>}
      {outs.map((h, i) => (
        <div key={h} className="port-row out" style={{ top: HEAD + i * ROW }}>
          <span>{h === "next" ? "" : h.startsWith("route:") ? h.slice(6) : h}</span>
          <Handle type="source" id={h} position={Position.Right} className="h-flow" style={{ top: 9 }} title={h} />
        </div>
      ))}
    </div>
  );
});

export const ResourceNode = memo(function ResourceNode({ id, data, selected }: NodeProps<Node<CanvasData>>) {
  const out = data.manifest?.outputs?.[0];
  const inputs = data.manifest?.inputs ?? []; // e.g. fallback models, a subagent's model and tools
  useHandleSync(id, [out?.name ?? "", ...inputs.map((p) => p.name)].join("|"));
  return (
    <div className={`resource${selected ? " selected" : ""}${inputs.length ? " with-inputs" : ""}`}
      style={{ borderColor: PORT_COLORS[out?.type ?? ""] ?? "#94a3b8" }} data-testid={`node-${data.name}`}>
      <div className="resource-head">
        <span className="icon">{ICONS[data.manifest?.icon ?? "box"] ?? "▪"}</span>
        <span className="title" title={data.type}>{data.name}</span>
        <em>{out?.type}</em>
        <Badges data={data} />
      </div>
      {inputs.map((p) => (
        <div key={p.name} className="resource-port">
          <Handle type="target" id={p.name} position={Position.Left} className="h-port"
            style={{ background: PORT_COLORS[p.type] ?? "#94a3b8" }} title={`${p.name}: ${p.type}${p.multi ? "[]" : ""}`} />
          <span>{p.name}{p.required ? "*" : ""}</span> <em>{p.type}{p.multi ? "[]" : ""}</em>
        </div>
      ))}
      {out && (
        <Handle type="source" id={out.name} position={Position.Right} className="h-port"
          style={{ background: PORT_COLORS[out.type] ?? "#94a3b8", top: 17 }} title={`${out.name}: ${out.type}`} />
      )}
    </div>
  );
});

export const TerminalNode = memo(function TerminalNode({ data }: NodeProps<Node<CanvasData>>) {
  const start = data.name === "START";
  return (
    <div className={`terminal ${start ? "start" : "end"}`} data-testid={`node-${data.name}`}>
      {start ? "▶ START" : "■ END"}
      {start
        ? <Handle type="source" id="next" position={Position.Right} className="h-flow" />
        : <Handle type="target" id="in" position={Position.Left} className="h-flow" />}
    </div>
  );
});

export const NODE_TYPES = { widget: WidgetNode, resource: ResourceNode, terminal: TerminalNode };
