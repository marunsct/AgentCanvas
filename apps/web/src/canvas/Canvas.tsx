/** The canvas (M2-T3…T6): derived from the IR, edits become commands. */
import {
  applyEdgeChanges, applyNodeChanges, Background, Controls, type Connection, type Edge, type EdgeChange, MiniMap,
  type Node, type NodeChange, ReactFlow, useNodesInitialized, useReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { type DragEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ulid } from "ulid";
import { useManifests } from "../api/queries";
import type { NodeManifest } from "../api/types";
import type { Command } from "../ir/commands";
import { allNames, defaultsFromSchema, uniqueName } from "../ir/model";
import { useEditor } from "../editor/store";
import { useRun } from "../run/runStore";
import { useUi } from "../editor/ui";
import { type Clip, copySelection, pasteCommands } from "./clipboard";
import { checkConnection, flowPort, nextOrder } from "./connect";
import { tidyLayout } from "./layout";
import { type CanvasData, irToFlow } from "./irToFlow";
import { NODE_TYPES } from "./nodes";

export const DND_MIME = "application/agentcanvas";
let clip: Clip | null = null; // in-memory clipboard shared by all canvases in this tab

export function Canvas() {
  const wf = useEditor((s) => s.wf);
  const layout = useEditor((s) => s.layout);
  const diagnostics = useEditor((s) => s.diagnostics);
  const selected = useEditor((s) => s.selected);
  const hint = useEditor((s) => s.connectHint);
  const { dispatch, select, setPositions, setConnectHint, setFocusNode } = useEditor.getState();
  const status = useRun((s) => s.status);
  const highlight = useRun((s) => s.highlight);
  const output = useRun((s) => s.output);
  const breakpoints = useEditor((s) => s.breakpoints);
  const manifests = useManifests();
  const rf = useReactFlow();

  const derived = useMemo(
    () => (wf
      ? irToFlow(wf, layout, manifests, diagnostics, status, selected, { highlight, breakpoints, output })
      : { nodes: [], edges: [] }),
    [wf, layout, manifests, diagnostics, status, selected, highlight, breakpoints, output],
  );
  const [nodes, setNodes] = useState<Node<CanvasData>[]>(derived.nodes);
  const [edges, setEdges] = useState<Edge[]>(derived.edges);
  const lastHint = useRef<string | null>(null);
  const initialized = useNodesInitialized();
  const fitted = useRef(false);

  // fit once, after React Flow has measured every node (fitting earlier zooms in on a partial graph)
  useEffect(() => {
    if (initialized && !fitted.current) {
      fitted.current = true;
      void rf.fitView({ padding: 0.15, maxZoom: 1 });
    }
  }, [initialized, rf]);

  // keep React Flow's measurements when the IR changes
  useEffect(() => {
    setNodes((prev) => {
      const byId = new Map(prev.map((n) => [n.id, n]));
      return derived.nodes.map((n) => {
        const old = byId.get(n.id);
        return old ? { ...n, measured: old.measured, width: old.width, height: old.height, dragging: old.dragging } : n;
      });
    });
    setEdges((prev) => {
      const sel = new Set(prev.filter((e) => e.selected).map((e) => e.id));
      return derived.edges.map((e) => ({ ...e, selected: sel.has(e.id) }));
    });
  }, [derived]);

  useEffect(() => {
    setFocusNode((id) => {
      select(id);
      void rf.fitView({ nodes: [{ id }], duration: 300, maxZoom: 1.2 });
    });
    return () => setFocusNode(null);
  }, [rf, select, setFocusNode]);

  // "Tidy up" (ELK) needs React Flow's measured sizes, so the canvas registers it
  useEffect(() => {
    useEditor.getState().setTidy(async () => {
      const positions = await tidyLayout(rf.getNodes(), rf.getEdges());
      useEditor.getState().setPositions(positions, true);
      requestAnimationFrame(() => void rf.fitView({ padding: 0.15, maxZoom: 1, duration: 300 }));
    });
    return () => useEditor.getState().setTidy(null);
  }, [rf]);

  // copy / paste (M2-T10); ignored while typing in a form field
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).closest("input, textarea, select, .code-view")) return;
      if (!(e.metaKey || e.ctrlKey)) return;
      const st = useEditor.getState();
      if (e.key.toLowerCase() === "c" && st.wf) {
        const ids = rf.getNodes().filter((n) => n.selected).map((n) => n.id);
        clip = copySelection(st.wf, ids, st.layout);
      } else if (e.key.toLowerCase() === "v" && clip && st.wf) {
        const { commands, positions, ids } = pasteCommands(st.wf, clip);
        st.dispatch(commands);
        st.setPositions(positions, true);
        if (ids[0]) st.select(ids[0]);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [rf]);

  const onNodesChange = useCallback((changes: NodeChange<Node<CanvasData>>[]) => {
    setNodes((ns) => applyNodeChanges(changes, ns));
    const moved: Record<string, { x: number; y: number }> = {};
    let done = false;
    for (const c of changes) {
      if (c.type === "position" && c.position) {
        moved[c.id] = { x: Math.round(c.position.x), y: Math.round(c.position.y) };
        if (c.dragging === false) done = true;
      }
      if (c.type === "select" && c.selected) select(c.id === "START" || c.id === "END" ? null : c.id);
    }
    if (Object.keys(moved).length) setPositions(moved, done);
  }, [select, setPositions]);

  const onEdgesChange = useCallback((changes: EdgeChange[]) => setEdges((es) => applyEdgeChanges(changes, es)), []);

  const onDelete = useCallback(({ nodes: ns, edges: es }: { nodes: Node<CanvasData>[]; edges: Edge[] }) => {
    const cmds: Command[] = [];
    const removed = new Set(ns.map((n) => n.id));
    for (const n of ns) {
      if (n.data.kind === "node") cmds.push({ op: "RemoveNode", id: n.id });
      if (n.data.kind === "resource") cmds.push({ op: "RemoveResource", id: n.id });
    }
    for (const e of es) if (!removed.has(e.source) && !removed.has(e.target)) cmds.push({ op: "Disconnect", id: e.id });
    if (cmds.length) dispatch(cmds);
    if (ns.some((n) => n.id === useEditor.getState().selected)) select(null);
  }, [dispatch, select]);

  const isValidConnection = useCallback((c: Connection | Edge) => {
    if (!wf) return false;
    const v = checkConnection(wf, manifests, c as Connection);
    const next = v.ok ? null : v.reason ?? "not allowed";
    if (next !== lastHint.current) {
      lastHint.current = next;
      setConnectHint(next);
    }
    return v.ok;
  }, [wf, manifests, setConnectHint]);

  const onConnect = useCallback((c: Connection) => {
    if (!wf) return;
    const v = checkConnection(wf, manifests, c);
    if (!v.ok) return;
    const id = `e_${ulid()}`;
    const edge = v.kind === "wiring"
      ? {
          id, kind: "wiring" as const, source: { node: c.source, port: c.sourceHandle },
          target: { node: c.target, port: c.targetHandle }, order: nextOrder(wf, c.target, c.targetHandle),
        }
      : { id, kind: "flow" as const, target: { node: c.target, port: null }, source: { node: c.source, port: flowPort(c.sourceHandle) } };
    dispatch([{ op: "Connect", edge }]);
  }, [wf, manifests, dispatch]);

  const onDrop = useCallback((ev: DragEvent) => {
    ev.preventDefault();
    const type = ev.dataTransfer.getData(DND_MIME);
    if (!wf || !manifests.has(type)) return;
    addElement(type, rf.screenToFlowPosition({ x: ev.clientX, y: ev.clientY }));
  }, [wf, manifests, rf]);

  return (
    <div className="canvas" onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = "move"; }} onDrop={onDrop}>
      <ReactFlow
        nodes={nodes} edges={edges} nodeTypes={NODE_TYPES}
        onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onDelete={onDelete}
        onConnect={onConnect} isValidConnection={isValidConnection}
        onConnectEnd={() => { lastHint.current = null; setTimeout(() => setConnectHint(null), 1500); }}
        onPaneClick={() => select(null)}
        onDoubleClick={(e) => {
          if (!(e.target as HTMLElement).classList.contains("react-flow__pane")) return;
          useUi.getState().openPalette(rf.screenToFlowPosition({ x: e.clientX, y: e.clientY }));
        }}
        zoomOnDoubleClick={false}
        deleteKeyCode={["Backspace", "Delete"]} minZoom={0.1} proOptions={{ hideAttribution: false }}
      >
        <Background gap={16} />
        <MiniMap pannable zoomable />
        <Controls />
      </ReactFlow>
      {hint && <div className="connect-hint" role="status">✋ {hint}</div>}
      {wf && wf.nodes.length === 0 && (
        <div className="empty-hint">Drag a widget from the palette, or start from a template on the home page.</div>
      )}
    </div>
  );
}

/** Create a node or resource of `type` at `position` (used by drag & drop and the palette's click-to-add). */
export function addElement(type: string, position?: { x: number; y: number }) {
  const { wf, dispatch, setPositions, select } = useEditor.getState();
  const m = manifestCache.get(type);
  if (!wf || !m) return;
  const name = uniqueName(m.label, allNames(wf));
  const config = defaultsFromSchema(m.config_schema as Record<string, unknown>);
  const cmd: Command = m.kind === "resource"
    ? { op: "AddResource", resource: { id: `r_${ulid()}`, type, name, config } }
    : { op: "AddNode", node: { id: `n_${ulid()}`, type, name, label: m.label, config } };
  const id = cmd.op === "AddResource" ? cmd.resource.id : cmd.op === "AddNode" ? cmd.node.id : "";
  dispatch([cmd]);
  if (position) setPositions({ [id]: { x: Math.round(position.x), y: Math.round(position.y) } }, true);
  select(id);
}

/** Manifests by type, filled by the palette once node types are loaded. */
export const manifestCache = new Map<string, NodeManifest>();
