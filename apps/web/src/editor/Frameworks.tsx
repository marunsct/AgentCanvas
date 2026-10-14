/** S20: which framework a workflow (and an AI block) is designed for, and what each installed framework can run.
 * S21: an Agent block can also be designed as a native agent of an agent framework (OpenAI Agents, CrewAI, ...). */
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { apiDownload, apiFetch, ApiError } from "../api/client";
import { useAgentFrameworks, useFrameworks } from "../api/queries";
import type { AgentCodeOut } from "../api/types";
import type { IRNode } from "../ir/model";
import { ENGINE_TYPES, portability } from "../ir/portability";
import { useEditor } from "./store";

/** The workflow's framework, in the workflow settings (click empty canvas). */
export function FrameworkPicker() {
  const framework = useEditor((s) => s.wf?.framework ?? "langchain");
  const dispatch = useEditor((s) => s.dispatch);
  const { data: frameworks = [] } = useFrameworks();
  const current = frameworks.find((f) => f.id === framework);
  return (
    <label>
      Framework
      <select aria-label="framework" value={framework}
        onChange={(e) => dispatch([{ op: "UpdateMeta", framework: e.target.value }])}>
        {!current && <option value={framework}>{framework} (not installed)</option>}
        {frameworks.map((f) => <option key={f.id} value={f.id}>{f.label}</option>)}
      </select>
      {current?.description && <small className="muted">{current.description}</small>}
      {current && current.ready === false && (
        <small className="warn-text" role="note" data-testid="framework-setup">
          Not set up on this server, so runs stop with an error. {current.setup}
        </small>
      )}
    </label>
  );
}

/** One badge per installed framework: can this workflow run there, and which blocks stop it. */
export function FrameworkBadges() {
  const wf = useEditor((s) => s.wf);
  const select = useEditor((s) => s.select);
  const { data: frameworks = [] } = useFrameworks();
  const { data: agents = [] } = useAgentFrameworks();
  const fits = useMemo(
    () => (wf ? portability(wf, frameworks, new Set(agents.map((a) => a.id))) : []), [wf, frameworks, agents],
  );
  const [open, setOpen] = useState<{ left: number } | null>(null);
  if (!wf || fits.length === 0) return null;
  const ready = new Map(frameworks.map((f) => [f.id, f.ready !== false]));
  // the top bar shows the workflow's own framework; the others open from "+N" so the bar fits a laptop window
  const shown = open ? fits : fits.filter((f) => f.id === wf.framework);
  const others = fits.filter((f) => f.id !== wf.framework);
  const otherOk = others.filter((f) => f.ok).length;
  return (
    <div className={`framework-badges${open ? " open" : ""}`} role="list" aria-label="frameworks this workflow can run on"
      style={open ? { left: open.left } : undefined}>
      {shown.map((f) => {
        const mine = f.id === wf.framework;
        const names = f.blockers.map((b) => b.name);
        const setUp = ready.get(f.id) !== false;
        const title = (f.ok
          ? `${f.label} can run every block${f.emulated.length ? ` (imitates ${f.emulated.map((b) => b.name).join(", ")})` : ""}`
          : `${f.label} cannot run: ${names.join(", ")}`) + (setUp ? "" : ". Not set up on this server yet (see the Framework setting)");
        return (
          <span key={f.id} role="listitem"><button type="button" title={title}
            className={`framework-badge ${f.ok ? "ok" : "blocked"}${mine ? " mine" : ""}`}
            aria-label={`${f.label}: ${f.ok ? "ready" : `${names.length} block${names.length === 1 ? "" : "s"} it cannot run`}${mine ? " (this workflow's framework)" : ""}`}
            onClick={() => f.blockers[0] && select(f.blockers[0].id)}>
            {f.ok ? "✓" : "✕"} {f.label}{f.ok ? "" : ` · ${names.length}`}{setUp ? "" : " · not set up"}
          </button></span>
        );
      })}
      {others.length > 0 && (
        <span role="listitem"><button type="button" className="framework-badge more" data-testid="framework-more"
          aria-expanded={Boolean(open)}
          onClick={(e) => setOpen(open ? null : { left: Math.max(14, (e.currentTarget.closest("[role=list]")?.getBoundingClientRect().left ?? 14) - 8) })}
          title={open ? "Show only this workflow's framework" : `${otherOk} of ${others.length} other frameworks can run this workflow`}>
          {open ? "less" : `+${others.length} (${otherOk} ✓)`}
        </button></span>
      )}
    </div>
  );
}

/** IR 1.2: an AI block may be designed for another framework than the workflow's. */
export function EnginePicker({ node }: { node: IRNode }) {
  const framework = useEditor((s) => s.wf?.framework ?? "langchain");
  const dispatch = useEditor((s) => s.dispatch);
  const { data: frameworks = [] } = useFrameworks();
  const { data: agents = [] } = useAgentFrameworks();
  const agentChoices = node.type === "core.agent" ? agents : [];
  if (!ENGINE_TYPES.has(node.type) || (frameworks.length < 2 && agentChoices.length === 0 && !node.engine)) return null;
  const workflowLabel = frameworks.find((f) => f.id === framework)?.label ?? framework;
  const known = !node.engine || frameworks.some((f) => f.id === node.engine) || agentChoices.some((a) => a.id === node.engine);
  const others = frameworks.filter((f) => f.id !== framework || node.engine === f.id);
  return (
    <label>
      Designed for
      <select aria-label="block framework" value={node.engine ?? ""}
        onChange={(e) => dispatch([{ op: "UpdateNode", id: node.id, patch: { engine: e.target.value || null } }])}>
        <option value="">Same as the workflow ({workflowLabel})</option>
        {!known && <option value={node.engine ?? ""}>{node.engine} (not available)</option>}
        {others.length > 0 && (
          <optgroup label="Workflow frameworks">
            {others.map((f) => <option key={f.id} value={f.id}>{f.label}</option>)}
          </optgroup>
        )}
        {agentChoices.length > 0 && (
          <optgroup label="Agent frameworks (generated as that framework's own code)">
            {agentChoices.map((a) => (
              <option key={a.id} value={a.id}>{a.label}{a.installed ? "" : " (not installed here)"}</option>
            ))}
          </optgroup>
        )}
      </select>
    </label>
  );
}

/** S21: an Agent block designed for an agent framework: what it runs as, its code, and a project to take away. */
export function DesignedAgent({ node }: { node: IRNode }) {
  const workflowId = useEditor((s) => s.workflowId);
  const irHash = useEditor((s) => s.irHash);
  const { data: agents = [] } = useAgentFrameworks();
  const [showCode, setShowCode] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const fw = agents.find((a) => a.id === node.engine);
  const code = useQuery({
    queryKey: ["agent-code", workflowId, node.id, irHash],
    queryFn: () => apiFetch<AgentCodeOut>(`/workflows/${workflowId}/agents/${node.id}/code`),
    enabled: Boolean(workflowId && fw),
    retry: false,
  });
  if (node.type !== "core.agent" || !fw) return null;
  const data = code.data;
  const exportBlocked = (data?.export_problems.length ?? 0) > 0;
  const download = () => {
    setExportError(null);
    apiDownload(`/workflows/${workflowId}/agents/${node.id}/export.zip`, `${node.name}-${fw.id}.zip`)
      .catch((e: unknown) => setExportError(e instanceof ApiError ? e.message : String(e)));
  };
  return (
    <section className="designed-agent" aria-label="designed agent">
      <p className="muted">
        Runs as an agent built with <b>{fw.label}</b> ({fw.packages.join(", ")}). Its model calls still go through this
        workflow&apos;s model, so runs are traced and costed as usual.
        {!fw.installed && " This server does not have it installed, so runs here will fail until it is."}
      </p>
      {fw.own_env && (
        <p className="muted" data-testid="part-note">
          {fw.label}&apos;s packages clash with LangGraph&apos;s, so this agent runs as a <b>part</b>: in an environment of
          its own, here and in an exported project. Its answer crosses back through a typed contract (
          {node.config?.response_format ? "its structured output, checked" : "text"}).
        </p>
      )}
      {data && data.problems.length > 0 && (
        <ul className="diag-list">
          {data.problems.map((p) => <li key={p} className="error">{p}</li>)}
        </ul>
      )}
      {code.error && <p className="error">{code.error instanceof ApiError ? code.error.message : String(code.error)}</p>}
      <div className="row gap">
        <button type="button" aria-expanded={showCode} onClick={() => setShowCode((v) => !v)}>
          {showCode ? "Hide" : "View"} {fw.label} code
        </button>
        <button type="button" disabled={!data || exportBlocked} onClick={download}
          title={exportBlocked ? data?.export_problems.join("\n") : "A runnable project with pinned packages, a README and tests"}>
          Export as {fw.label} project
        </button>
      </div>
      {exportBlocked && data && data.problems.length === 0 && (
        <ul className="diag-list">
          {data.export_problems.map((p) => <li key={p} className="warning">{p}</li>)}
        </ul>
      )}
      {exportError && <p className="error">{exportError}</p>}
      {showCode && (code.isFetching && !data ? <p className="muted">Generating…</p> : data && (
        <pre className="code designed-code" aria-label={`${fw.label} code`}>{data.code}</pre>
      ))}
    </section>
  );
}
