/** Manifest-driven node renderers (M2-T5). One generic widget draws every node type from its manifest. */
import { Handle, type Node, type NodeProps, Position, useUpdateNodeInternals } from "@xyflow/react";
import { memo, type MouseEvent, type KeyboardEvent, useEffect, useRef } from "react";
import type { NodeManifest } from "../api/types";
import { PeerMarks } from "../editor/PresenceBar";
import { useUi } from "../editor/ui";
import { type PortHelp, portHelp, typeName } from "../help/glossary";
import { rememberOpener } from "./focus";
import { type CanvasData, PORT_COLORS } from "./irToFlow";
import { onActivate } from "./keys";

const ICONS: Record<string, string> = {
  sparkles: "✨", bot: "🤖", split: "🔀", "user-check": "🙋", pencil: "✏️", code: "⌨️", cpu: "🔌", wrench: "🔧", box: "▪",
  brain: "🧠", shield: "🛡", gauge: "⏱", "rotate-cw": "🔁", "life-buoy": "🛟", scroll: "📜", "list-checks": "☑",
  filter: "🔎", file: "📄", database: "🗄", "message-square": "💬", "git-fork": "🔱", merge: "⛙", plug: "🔌", globe: "🌐", layers: "🗂",
  "book-open": "📚", users: "👥", "clipboard-check": "📋", hourglass: "⏳",
};
const STATUS_LABEL: Record<string, string> = { running: "running", ok: "done", error: "error", interrupted: "waiting" };

export const EFFECT_ICONS: Record<string, [string, string]> = {
  external_write: ["✎", "writes to external systems"],
  message_send: ["✉", "sends messages"],
  code_exec: ["⚙", "executes code"],
  spend: ["$", "spends money (model calls)"],
  pii_read: ["👁", "reads personal data"],
};

function Badges({ data }: { data: CanvasData }) {
  return (
    <span className="badges">
      {data.framework && (
        <span className="badge framework" title={data.framework.title} data-testid={`framework-${data.name}`}>
          {data.framework.text}
        </span>
      )}
      {data.blast && (
        <span className={`badge blast${data.blast.ungated ? " err" : ""}`} data-testid={`blast-${data.name}`}
          title={`${data.blast.effects.map((e) => EFFECT_ICONS[e]?.[1] ?? e).join(", ")}${data.blast.ungated ? " — no human approval before it" : ""}`}>
          {data.blast.effects.map((e) => EFFECT_ICONS[e]?.[0] ?? "•").join("")}
        </span>
      )}
      {data.status !== "idle" && <span className={`badge status-${data.status}`}>{STATUS_LABEL[data.status]}</span>}
      {data.errors > 0 && <span className="badge err" title={`${data.errors} error(s)`}>⛔ {data.errors}</span>}
      {data.warnings > 0 && <span className="badge warn" title={`${data.warnings} warning(s)`}>⚠ {data.warnings}</span>}
    </span>
  );
}

/** Hover card for a port: what it carries and what to do with it (shown by CSS on :hover of its row). */
function PortTip({ help, side }: { help: PortHelp; side: string }) {
  return (
    <span className={`port-tip ${side}`} role="tooltip">
      <b>{help.title}</b>
      <span>{help.body}</span>
      <small>{help.action}</small>
    </span>
  );
}

/** Clicking a port (or pressing Enter/Space on it) opens "what can connect here?" (replaces React Flow's click-to-connect). */
function openMenu(node: string, handle: string, side: "in" | "out") {
  const open = (x: number, y: number) => useUi.getState().openPortMenu({ node, handle, side, x, y });
  return {
    onClick: (e: MouseEvent) => { e.stopPropagation(); rememberOpener(null); open(e.clientX, e.clientY); },
    onKeyDown: (e: KeyboardEvent<HTMLElement>) => {
      const el = e.currentTarget;
      onActivate(() => { rememberOpener(el); const r = el.getBoundingClientRect(); open(r.right, r.top + r.height / 2); })(e);
    },
    tabIndex: 0,
    role: "button",
    "aria-label": `${side === "in" ? "input" : "output"} ${handle}: show what can connect`,
  } as const;
}

/** Hover text for a block's title: what it does, in plain words. */
function blockTitle(m: NodeManifest | undefined, type: string) {
  return m?.description ? `${m.label}: ${m.description}\n(${type})` : type;
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
      className={`widget status-${data.status}${selected ? " selected" : ""}${data.disabled ? " disabled" : ""}${data.highlight ? " highlight" : ""}${data.diff ? ` diff-${data.diff}` : ""}${data.blast?.ungated ? " blast-ungated" : ""}`}
      style={{ borderTopColor: m?.color ?? "#64748b", minHeight: HEAD + rows * ROW + 8 + (data.preview ? 34 : 0) }}
      data-testid={`node-${data.name}`} role="group" aria-label={`${data.label} (${data.type})${data.errors ? `, ${data.errors} errors` : ""}`}
    >
      <Handle type="target" id="in" position={Position.Left} className="h-flow" style={{ top: 20 }} />
      <PortTip help={portHelp(m, undefined, "in")} side="in flow-in" />
      <PeerMarks id={id} />
      <header>
        <span className="icon">{ICONS[m?.icon ?? "box"] ?? "▪"}</span>
        <span className="title" title={blockTitle(m, data.type)}>{data.label}</span>
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
            style={{ top: 9, background: PORT_COLORS[p.type] ?? "#94a3b8" }} {...openMenu(id, p.name, "in")}
          />
          <span>{p.name}{p.required ? "*" : ""}</span>
          <em>{typeName(p.type)}{p.multi ? "s" : ""}</em>
          <PortTip help={portHelp(m, p, "in")} side="in" />
        </div>
      ))}
      {data.preview && <div className="preview" style={{ top: HEAD + rows * ROW }} title={data.preview}>{data.preview}</div>}
      {outs.map((h, i) => (
        <div key={h} className="port-row out" style={{ top: HEAD + i * ROW }}>
          <span>{h === "next" ? "next" : h.startsWith("route:") ? h.slice(6) : h}</span>
          <Handle type="source" id={h} position={Position.Right} className="h-flow" style={{ top: 9 }}
            {...openMenu(id, h, "out")} />
          <PortTip help={portHelp(m, m?.outputs?.find((p) => p.name === h), "out", h)} side="out" />
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
    <div className={`resource${selected ? " selected" : ""}${inputs.length ? " with-inputs" : ""}${data.diff ? ` diff-${data.diff}` : ""}`}
      style={{ borderColor: PORT_COLORS[out?.type ?? ""] ?? "#94a3b8" }} data-testid={`node-${data.name}`}
      role="group" aria-label={`${data.name} (${data.type})`}>
      <PeerMarks id={id} />
      <div className="resource-head">
        <span className="icon">{ICONS[data.manifest?.icon ?? "box"] ?? "▪"}</span>
        <span className="title" title={blockTitle(data.manifest, data.type)}>{data.name}</span>
        <em>{typeName(out?.type ?? "")}</em>
        <Badges data={data} />
      </div>
      {inputs.map((p) => (
        <div key={p.name} className="resource-port">
          <Handle type="target" id={p.name} position={Position.Left} className="h-port"
            style={{ background: PORT_COLORS[p.type] ?? "#94a3b8" }} {...openMenu(id, p.name, "in")} />
          <span>{p.name}{p.required ? "*" : ""}</span> <em>{typeName(p.type)}{p.multi ? "s" : ""}</em>
          <PortTip help={portHelp(data.manifest, p, "in")} side="in" />
        </div>
      ))}
      {out && (
        <>
          <Handle type="source" id={out.name} position={Position.Right} className="h-port"
            style={{ background: PORT_COLORS[out.type] ?? "#94a3b8", top: 17 }} {...openMenu(id, out.name, "out")} />
          <PortTip help={portHelp(data.manifest, out, "out")} side="out resource-out" />
        </>
      )}
    </div>
  );
});

export const TerminalNode = memo(function TerminalNode({ data }: NodeProps<Node<CanvasData>>) {
  const start = data.name === "START";
  return (
    <div className={`terminal ${start ? "start" : "end"}`} data-testid={`node-${data.name}`}
      title={start ? "Every run begins here. Connect it to your first step." : "The run finishes when it reaches here. Connect your last step to it."}>
      {start ? "▶ Start" : "■ Finish"}
      {start
        ? <Handle type="source" id="next" position={Position.Right} className="h-flow" {...openMenu("START", "next", "out")} />
        : <Handle type="target" id="in" position={Position.Left} className="h-flow" />}
    </div>
  );
});

export const NODE_TYPES = { widget: WidgetNode, resource: ResourceNode, terminal: TerminalNode };
