import { useEffect, useMemo, useState } from "react";
import { Background, Controls, MiniMap, ReactFlow } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { Workflow } from "./ir";
import { irToFlow } from "./irToFlow";
import { useRun, type NodeStatus } from "./runStore";
import { postStream } from "./sse";

const COLORS: Record<NodeStatus, string> = {
  idle: "#e5e7eb", running: "#3b82f6", ok: "#22c55e", error: "#ef4444", interrupted: "#f59e0b",
};

const PROJECT = "prj_default";

/** Find the demo workflow in the default project, or create it from the `support_triage` template. */
async function loadDemoWorkflow(): Promise<Workflow> {
  const list = await (await fetch(`/api/v1/projects/${PROJECT}/workflows`)).json();
  let id: string | undefined = list.items.find((w: { name: string }) => w.name === "Support triage")?.id;
  if (!id) {
    const created = await fetch(`/api/v1/projects/${PROJECT}/workflows`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ name: "Support triage", template: "support_triage" }),
    });
    id = (await created.json()).id;
  }
  return (await (await fetch(`/api/v1/workflows/${id}`)).json()).ir as Workflow;
}

export default function App() {
  const [wf, setWf] = useState<Workflow | null>(null);
  const [ticket, setTicket] = useState("Where is my invoice?");
  const run = useRun();

  useEffect(() => {
    loadDemoWorkflow().then(setWf).catch(console.error);
  }, []);

  const flow = useMemo(() => (wf ? irToFlow(wf) : { nodes: [], edges: [] }), [wf]);
  const nodes = flow.nodes.map((n) => ({
    ...n,
    style: { border: `3px solid ${COLORS[run.status[n.id] ?? "idle"]}`, borderRadius: 8, padding: 6 },
  }));

  const start = () => {
    run.reset();
    if (!wf) return;
    void postStream(`/api/v1/workflows/${wf.id}/runs/stream`, { input: { ticket } }, run.apply);
  };
  const decide = (value: unknown) => {
    if (!run.threadId) return;
    void postStream(`/api/v1/threads/${run.threadId}/resume`, { value, interrupt_id: run.interrupt?.id }, run.apply);
  };
  const isToolApproval = typeof run.interrupt?.payload === "object" && run.interrupt?.payload !== null
    && "action_requests" in (run.interrupt.payload as object);

  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 320px", height: "100vh" }}>
      <ReactFlow nodes={nodes} edges={flow.edges} fitView>
        <Background /><MiniMap /><Controls />
      </ReactFlow>
      <aside style={{ padding: 16, borderLeft: "1px solid #ddd", fontFamily: "sans-serif" }}>
        <h3>{wf?.name ?? "Loading…"}</h3>
        <textarea value={ticket} onChange={(e) => setTicket(e.target.value)} rows={3} style={{ width: "100%" }} />
        <button onClick={start} disabled={run.running}>▶ Run</button>
        {run.error && <p style={{ color: "#b91c1c" }}>{run.error}</p>}
        {run.interrupt && (
          <div style={{ marginTop: 16, padding: 8, background: "#fef3c7" }}>
            <b>Approval needed</b>
            <pre style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(run.interrupt.payload, null, 2)}</pre>
            <button onClick={() => decide(isToolApproval ? { decisions: [{ type: "approve" }] } : { approved: true })}>Approve</button>{" "}
            <button onClick={() => decide(isToolApproval ? { decisions: [{ type: "reject", message: "Rejected by reviewer" }] } : { approved: false })}>Reject</button>
          </div>
        )}
        <h4>Node output</h4>
        {Object.entries(run.output).map(([id, text]) => <p key={id}><code>{id}</code>: {text}</p>)}
      </aside>
    </div>
  );
}
