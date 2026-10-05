/** The canvas (M2-T3…T6): derived from the IR, edits become commands. */
import {
  applyEdgeChanges, applyNodeChanges, Background, Controls, type Connection, type Edge, type EdgeChange, MiniMap,
  type Node, type NodeChange, ReactFlow, useNodesInitialized, useReactFlow, useUpdateNodeInternals,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { type DragEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ulid } from "ulid";
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import type { BlastRadius } from "../api/generated";
import { useManifests } from "../api/queries";
import type { NodeManifest } from "../api/types";
import type { Command } from "../ir/commands";
import { allNames, defaultsFromSchema, innerWorkflow, uniqueName } from "../ir/model";
import { useEditor } from "../editor/store";
import { useRun } from "../run/runStore";
import { useUi } from "../editor/ui";
import { QueryStatus } from "../ui/QueryStatus";
import { type Clip, copySelection, pasteCommands } from "./clipboard";
import { checkConnection, flowPort, nextOrder } from "./connect";
import { tidyLayout } from "./layout";
import { type CanvasData, irToFlow } from "./irToFlow";
import { Frames, frameSelection } from "./Frames";
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
  const metrics = useRun((s) => s.metrics);
  const breakpoints = useEditor((s) => s.breakpoints);
  const pins = useEditor((s) => s.pins);
  const diff = useUi((s) => s.diff);
  const showBlast = useUi((s) => s.blast);
  const irHash = useEditor((s) => s.irHash);
  const blastQuery = useQuery({
    queryKey: ["blast", wf?.id, irHash], enabled: Boolean(wf && showBlast),
    queryFn: () => apiFetch<BlastRadius>(`/workflows/${wf!.id}/blast-radius`),
  });
  const blast = useMemo(() => {
    if (!showBlast || !blastQuery.data) return null;
    const ungated = new Set(blastQuery.data.ungated ?? []);
    return Object.fromEntries((blastQuery.data.nodes ?? []).map((n) => [
      n.node_id, { effects: [...new Set((n.capabilities ?? []).map((c) => c.effect))], ungated: ungated.has(n.node_id) },
    ]));
  }, [showBlast, blastQuery.data]);
  const manifests = useManifests();
  const rf = useReactFlow();

  const drill = useEditor((s) => s.drill);
  // drill-in (M2-T13): the canvas shows a subgraph's steps read-only, with live status from nested events
  const view = useMemo(() => (wf && drill.length ? innerWorkflow(wf, drill) : wf), [wf, drill]);
  const readOnly = drill.length > 0;
  useEffect(() => {
    if (readOnly && !view) useEditor.getState().setDrill([]); // the subgraph was removed or inlined
  }, [readOnly, view]);
  const derived = useMemo(
    () => (view
      ? irToFlow(view, readOnly ? {} : layout, manifests, readOnly ? [] : diagnostics, status, readOnly ? null : selected,
        { highlight, breakpoints, output, metrics, pins, diff: readOnly ? null : diff, blast: readOnly ? null : blast })
      : { nodes: [], edges: [] }),
    [view, readOnly, layout, manifests, diagnostics, status, selected, highlight, breakpoints, output, metrics, pins, diff, blast],
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
  // entering or leaving a subgraph shows a different graph: fit it once it is measured
  useEffect(() => {
    const t = setTimeout(() => void rf.fitView({ padding: 0.2, maxZoom: 1, duration: 200 }), 60);
    return () => clearTimeout(t);
  }, [drill, rf]);

  // "Convert to subgraph" (M2-T13): the server computes the refactoring; it lands as ordinary, undoable commands
  useEffect(() => {
    useEditor.getState().setGroup(async () => {
      const st = useEditor.getState();
      if (st.drill.length || !st.wf) return;
      const picked = rf.getNodes().filter((n) => n.selected && n.data.kind === "node").map((n) => n.id);
      const chosen = picked.length ? picked : st.wf.nodes.some((n) => n.id === st.selected) ? [st.selected!] : [];
      if (!chosen.length) {
        st.setNotice({ kind: "info", text: "Select the steps to group first (Ctrl/⌘-click them or drag a selection box)." });
        return;
      }
      const at = st.layout[chosen[0]];
      const cmds = await st.refactor("subgraph", { node_ids: chosen });
      const add = cmds?.find((c) => c.op === "AddNode");
      if (add?.op === "AddNode") {
        if (at) st.setPositions({ [add.node.id]: at }, true);
        st.select(add.node.id);
      }
    });
    return () => useEditor.getState().setGroup(null);
  }, [rf]);

  // "Frame" (layout only): a labelled box around the selected steps and resources
  useEffect(() => {
    useEditor.getState().setFrame(() => {
      const st = useEditor.getState();
      if (st.drill.length) return;
      const picked = rf.getNodes().filter((n) => n.selected && n.data.kind !== "terminal").map((n) => n.id);
      const chosen = picked.length ? picked : st.selected ? [st.selected] : [];
      if (!chosen.length) {
        st.setNotice({ kind: "info", text: "Select the steps to frame first (Ctrl/⌘-click them or drag a selection box)." });
        return;
      }
      const label = window.prompt("Frame label", "Group")?.trim();
      if (label) frameSelection(chosen, label);
    });
    return () => useEditor.getState().setFrame(null);
  }, [rf]);

  // keep React Flow's measurements and its (multi-)selection when the IR changes; the editor's selected
  // element is always part of it (it is only ever *added* here: React Flow owns deselection)
  useEffect(() => {
    setNodes((prev) => {
      const byId = new Map(prev.map((n) => [n.id, n]));
      return derived.nodes.map((n) => {
        const old = byId.get(n.id);
        if (!old) return n;
        return {
          ...n, measured: old.measured, width: old.width, height: old.height, dragging: old.dragging,
          selected: Boolean(old.selected) || n.selected,
        };
      });
    });
    setEdges((prev) => {
      const sel = new Set(prev.filter((e) => e.selected).map((e) => e.id));
      return derived.edges.map((e) => ({ ...e, selected: sel.has(e.id) }));
    });
  }, [derived]);

  useEffect(() => {
    setFocusNode((id) => {
      setNodes((ns) => ns.map((n) => ({ ...n, selected: n.id === id }))); // focusing from elsewhere: only this one
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
    if (useEditor.getState().drill.length) return; // read-only view: nothing is saved
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

  // React Flow error 008: an edge references a handle that was not measured yet (a handle rendered after the
  // node's first measurement). Re-measure both ends a few times instead of leaving the edge invisible (FAQ F-03).
  const updateInternals = useUpdateNodeInternals();
  const remeasured = useRef(new Map<string, number>());
  const onError = useCallback((code: string, message: string) => {
    const edgeId = code === "008" ? /edge id: ([^\s.]+)/.exec(message)?.[1] : undefined;
    const edge = edgeId ? rf.getEdge(edgeId) : undefined;
    const tries = edgeId ? remeasured.current.get(edgeId) ?? 0 : 99;
    if (!edge || tries >= 5) {
      console.warn(`[React Flow ${code}] ${message}`);
      return;
    }
    remeasured.current.set(edgeId!, tries + 1);
    setTimeout(() => updateInternals([edge.source, edge.target]), 50 * (tries + 1));
  }, [rf, updateInternals]);

  const onDelete = useCallback(({ nodes: ns, edges: es }: { nodes: Node<CanvasData>[]; edges: Edge[] }) => {
    if (useEditor.getState().drill.length) return;
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
    if (!wf || useEditor.getState().drill.length) return false;
    const v = checkConnection(wf, manifests, c as Connection);
    const next = v.ok ? null : v.reason ?? "not allowed";
    if (next !== lastHint.current) {
      lastHint.current = next;
      setConnectHint(next);
    }
    return v.ok;
  }, [wf, manifests, setConnectHint]);

  const onConnect = useCallback((c: Connection) => {
    if (!wf || useEditor.getState().drill.length) return;
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
    const raw = ev.dataTransfer.getData(DND_MIME);
    const [type, presetJson] = raw.split("\n");
    if (!wf || !manifests.has(type) || useEditor.getState().drill.length) return;
    const preset = presetJson ? (JSON.parse(presetJson) as Preset) : undefined;
    addElement(type, rf.screenToFlowPosition({ x: ev.clientX, y: ev.clientY }), preset);
  }, [wf, manifests, rf]);

  return (
    <div className="canvas" onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = "move"; }} onDrop={onDrop}>
      <ReactFlow
        nodes={nodes} edges={edges} nodeTypes={NODE_TYPES}
        onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onDelete={onDelete}
        onConnect={onConnect} isValidConnection={isValidConnection} onError={onError}
        onConnectEnd={() => { lastHint.current = null; setTimeout(() => setConnectHint(null), 1500); }}
        onPaneClick={() => select(null)}
        onNodeDoubleClick={(_, n) => {
          if (n.data.type === "core.subgraph") useEditor.getState().setDrill([...useEditor.getState().drill, n.id]);
        }}
        nodesDraggable={!readOnly} nodesConnectable={!readOnly}
        onDoubleClick={(e) => {
          if (!(e.target as HTMLElement).classList.contains("react-flow__pane")) return;
          useUi.getState().openPalette(rf.screenToFlowPosition({ x: e.clientX, y: e.clientY }));
        }}
        zoomOnDoubleClick={false}
        deleteKeyCode={readOnly ? null : ["Backspace", "Delete"]} minZoom={0.1} proOptions={{ hideAttribution: false }}
      >
        <Frames />
        <Background gap={16} />
        <MiniMap pannable zoomable />
        <Controls />
      </ReactFlow>
      {hint && <div className="connect-hint" role="status">✋ {hint}</div>}
      {showBlast && !readOnly && (blastQuery.isLoading || blastQuery.isError) && (
        // a safety view: a failed request must not look like "no effects"
        <div className="blast-status" data-testid="blast-status"><QueryStatus q={blastQuery} what="the blast radius" /></div>
      )}
      {readOnly && <SubgraphCrumbs />}
      {wf && wf.nodes.length === 0 && (
        <div className="empty-hint">Drag a widget from the palette, or start from a template on the home page.</div>
      )}
    </div>
  );
}

/** A palette entry that pre-fills a generic type, e.g. a workspace tool = tool.python with its import path. */
export interface Preset {
  name?: string;
  config?: Record<string, unknown>;
}

/** Create a node or resource of `type` at `position` (used by drag & drop and the palette's click-to-add). */
export function addElement(type: string, position?: { x: number; y: number }, preset?: Preset) {
  const { wf, dispatch, setPositions, select } = useEditor.getState();
  const m = manifestCache.get(type);
  if (!wf || !m) return;
  const name = uniqueName(preset?.name ?? m.label, allNames(wf));
  const config = { ...defaultsFromSchema(m.config_schema as Record<string, unknown>), ...preset?.config };
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

/** Breadcrumb of the drill-in view: Workflow › subgraph › … (click to go back up). */
function SubgraphCrumbs() {
  const wf = useEditor((s) => s.wf);
  const drill = useEditor((s) => s.drill);
  if (!wf) return null;
  const names = drill.map((_, i) => {
    const parent = innerWorkflow(wf, drill.slice(0, i));
    const n = parent?.nodes.find((x) => x.id === drill[i]);
    return n?.label || n?.name || drill[i];
  });
  return (
    <nav className="subgraph-crumbs" aria-label="subgraph path">
      <button type="button" className="link" onClick={() => useEditor.getState().setDrill([])}>Workflow</button>
      {names.map((name, i) => (
        <span key={drill[i]}>
          {" › "}
          {i === names.length - 1
            ? <b>{name}</b>
            : <button type="button" className="link" onClick={() => useEditor.getState().setDrill(drill.slice(0, i + 1))}>{name}</button>}
        </span>
      ))}
      <small className="muted"> · read-only — to edit these steps, inline the subgraph</small>
    </nav>
  );
}
