# 06 — Frontend Guide

Stack (ADR 0006): React 19 · TypeScript (strict) · Vite · **@xyflow/react 12** (canvas) · Zustand (client state) · TanStack Query (server data) · **@rjsf/core 6** (config forms) · elkjs (auto-layout) · @monaco-editor/react (code) · Vitest + Playwright. Every snippet below type-checks with these versions (see `reference/web/snippets/verifyLibs.tsx` and `verifyGuide06.tsx`).

## 1. Folder structure

```
apps/web/src/
├── app/            App.tsx, routes.tsx, Layout.tsx, providers.tsx (QueryClientProvider, theme)
├── api/            client.ts (apiFetch + ApiError), schema.d.ts (generated), queries.ts (useWorkflow, useNodeTypes…)
├── ir/             generated.ts (from ir.schema.json), helpers.ts (find node, ports)
├── canvas/         Canvas.tsx, nodes/WidgetNode.tsx, edges/, Palette.tsx, irToFlow.ts, commands.ts, useAutosave.ts
├── inspector/      Inspector.tsx, StateDesigner.tsx, widgets/(PromptWidget|ChannelSelect|CelEditor).tsx
├── run/            sse.ts, runStore.ts, RunPanel.tsx, Timeline.tsx, StateViewer.tsx
├── inbox/          Inbox.tsx, DecisionCard.tsx
├── workspace/      FileTree.tsx, CodeEditor.tsx
└── shared/         ui/ (Button, Panel, Badge…), hooks/, utils/
```

## 2. Data flow

```
TanStack Query ──GET workflow──► useWorkflow() ──► irToFlow() ──► <ReactFlow nodes edges>
      ▲                                                               │ user edits
      │ invalidate on success                                          ▼
  POST /commands (If-Match) ◄── commandQueue (debounce 300 ms) ◄── commands.ts (AddNode, Connect…)
                                                                          │ optimistic apply
                                                                          ▼
                                                                  local IR copy (Zustand)
Run: postStream() ──SSE──► runStore.apply(event) ──► node status colours, outputs, interrupt card
```
Rules:
- The **IR is the model**; React Flow nodes/edges are *derived* (`irToFlow`). Never store business data only in React Flow state.
- Edits are **commands**, applied optimistically to the local IR and sent to the server; on `409` refetch and re-apply nothing (show toast).
- Node positions live in the separate layout document (M1-T5).

## 3. The API client

```ts
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
```

## 4. Custom node renderer (M2-T5)

One generic component draws every widget from its manifest:
```tsx
import { Handle, Position, type NodeProps } from "@xyflow/react";

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
// register once: <ReactFlow nodeTypes={{ widget: WidgetNode }} … />  (define nodeTypes outside components to avoid re-renders)
```

## 5. Type-checked connections (M2-T6)

```ts
import type { IsValidConnection } from "@xyflow/react";

export const makeIsValid = (portType: (nodeId: string, handle: string | null, dir: "in" | "out") => string | undefined)
  : IsValidConnection => (c) => {
    const out = portType(c.source, c.sourceHandle ?? null, "out");
    const inp = portType(c.target, c.targetHandle ?? null, "in");
    return !!out && !!inp && (out === inp || inp === "Any" || (inp === "Tool[]" && out === "Tool"));
  };
```
Show *why* a connection is refused: keep the last rejected pair in state and render a tooltip near the cursor.

## 6. Inspector forms with RJSF (M2-T7)

```tsx
import Form from "@rjsf/core";
import validator from "@rjsf/validator-ajv8";

<Form schema={manifest.config_schema} formData={node.config} validator={validator}
      widgets={{ prompt: PromptWidget, channel: ChannelSelect, cel: CelEditor }}
      uiSchema={uiSchemaFromFormats(manifest.config_schema)}   // maps "format": "prompt" -> "ui:widget": "prompt"
      onChange={(e) => queueCommand({ op: "UpdateNodeConfig", node_id: node.id, patch: e.formData })}
      liveValidate showErrorList={false} />
```
- `PromptWidget`: textarea + autocomplete of `{{ state.<channel> }}` + token estimate (`chars / 4`).
- `ChannelSelect`: `<select>` of state channel names.
- `CelEditor`: input that calls `POST /v1/expressions/check` (debounced) and shows the error.
- Hide the default submit button: pass `<Form …><></></Form>` children.

## 7. Auto-layout with ELK (M2-T11)

```ts
import ELK from "elkjs/lib/elk.bundled.js";
export async function layout(nodes: Node[], edges: Edge[]): Promise<Node[]> {
  const res = await new ELK().layout({
    id: "root", layoutOptions: { "elk.algorithm": "layered", "elk.direction": "RIGHT", "elk.spacing.nodeNode": "40" },
    children: nodes.map((n) => ({ id: n.id, width: n.measured?.width ?? 180, height: n.measured?.height ?? 60 })),
    edges: edges.map((e) => ({ id: e.id, sources: [e.source], targets: [e.target] })),
  });
  return nodes.map((n) => { const p = res.children?.find((c) => c.id === n.id); return { ...n, position: { x: p?.x ?? 0, y: p?.y ?? 0 } }; });
}
```

## 8. Run store & SSE (from the reference)

`reference/web/src/sse.ts` and `runStore.ts` are production-ready starting points:
- `postStream(url, body, onEvent)` — POST + incremental SSE parsing (handles messages split across network chunks).
- `reduce(state, event)` — **pure** function; unit-test every event type.
- Batch high-frequency `TEXT_MESSAGE_CONTENT` updates with `requestAnimationFrame` (M4-T3) to avoid 1,000 re-renders per second.

Status colours (keep consistent everywhere): idle `#e5e7eb` · running `#3b82f6` · ok `#22c55e` · error `#ef4444` · interrupted `#f59e0b` · cached `#9ca3af`.

## 9. Inbox decision card (M4-T4)

| Interrupt payload | UI | Resume value sent |
|-------------------|----|-------------------|
| `{"action_requests": [...], "review_configs": [...]}` (agent tool approval) | tool name, JSON args (editable if `edit` allowed), Approve / Edit / Reject (+ message) | `{"decisions": [{"type": "approve"}]}` · `{"decisions": [{"type": "edit", "edited_action": {"name": "...", "args": {...}}}]}` · `{"decisions": [{"type": "reject", "message": "..."}]}` |
| `{"kind": "approval", "value": ...}` | value preview (editable), Approve / Reject | `{"approved": true, "value": <edited or omitted>}` |
| `{"kind": "input", "response_schema": {...}}` | RJSF form | the form data |

One decision per pending action request, in the same order as `action_requests`.

## 10. Code editor (M5-T2)

```tsx
import Editor from "@monaco-editor/react";
<Editor height="100%" language="python" value={content} onChange={(v) => setDraft(v ?? "")}
        options={{ minimap: { enabled: false }, fontSize: 13 }} />
```
Show ruff diagnostics with `monaco.editor.setModelMarkers(model, "ruff", markers)` using the `onMount` callback to obtain `monaco`.

## 11. Accessibility & UX checklist
- Every button has a text label or `aria-label`; focus rings visible.
- Keyboard: `Tab` moves through nodes (`nodesFocusable`), `Delete` removes, `⌘Z` undo, `⌘K` palette.
- Colour is never the only signal: status also shown as an icon/text badge.
- Loading skeletons for data fetches; empty states with a call to action ("Drag a node from the palette").

## 12. Testing
- **Vitest:** `irToFlow`, `reduce`, command inverse functions, `makeIsValid`, form→command mapping.
- **Playwright:** skeleton flow (run → approve), build-a-workflow flow (drag, connect, configure, run), undo/redo, conflict toast. Use the fake-model backend (`agentcanvas.dev.fake_server`) in CI.
