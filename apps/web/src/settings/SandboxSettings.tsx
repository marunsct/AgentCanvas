/** Settings › Project › Sandbox: limits for workspace code, and the hosts it may reach (`sandbox.json`). The network
 * list is offered only where this server enforces it; elsewhere the page says why and what an operator changes. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { apiFetch } from "../api/client";
import type { SandboxOut } from "../api/generated";
import { PROJECT_ID } from "../api/queries";
import { useCan } from "../api/session";
import { toast } from "../ui/dialog";
import { QueryStatus } from "../ui/QueryStatus";

const MODES: Record<string, string> = {
  inprocess: "in the API's own process (development only: no isolation)",
  subprocess: "in a separate process with limits (no network, but not isolated from the server's files)",
  docker: "in a Docker container per call",
  remote: "in the sandbox service, a container of its own",
  langsmith: "in a LangSmith Sandbox",
};

export function SandboxSettings() {
  const qc = useQueryClient();
  const canBuild = useCan("builder");
  const url = `/projects/${PROJECT_ID}/sandbox`;
  const q = useQuery({ queryKey: ["sandbox", PROJECT_ID], queryFn: () => apiFetch<SandboxOut>(url) });
  const [hosts, setHosts] = useState<string[]>([]);
  const [draft, setDraft] = useState("");
  const [timeout, setTimeoutS] = useState(30);
  const [memory, setMemory] = useState(512);
  useEffect(() => {
    if (!q.data?.policy) return;
    setHosts((q.data.policy.egress as string[] | undefined) ?? []);
    setTimeoutS((q.data.policy.timeout_s as number | undefined) ?? 30);
    setMemory((q.data.policy.memory_mb as number | undefined) ?? 512);
  }, [q.data]);
  const save = useMutation({
    mutationFn: () => apiFetch<SandboxOut>(url, {
      method: "PUT",
      json: { ...(q.data?.policy ?? {}), egress: hosts, timeout_s: timeout, memory_mb: memory },
    }),
    onSuccess: (out) => { qc.setQueryData(["sandbox", PROJECT_ID], out); toast("Saved the sandbox settings"); },
  });
  if (!q.data) return <section className="settings-card"><h2>Sandbox</h2><QueryStatus q={q} what="the sandbox settings" /></section>;
  const d = q.data;
  const add = () => {
    const h = draft.trim().replace(/^https?:\/\//, "").replace(/\/.*$/, "");
    if (h && !hosts.includes(h)) setHosts([...hosts, h]);
    setDraft("");
  };
  const inject = (d.policy?.inject as unknown[] | undefined) ?? [];
  return (
    <section className="settings-card sandbox-settings" aria-label="Sandbox">
      <h2>Sandbox</h2>
      <p className="muted small">
        Workspace code (scripts, tools, custom blocks, event handlers) runs {MODES[d.mode] ?? d.mode}. These limits
        apply to every call; the server's operator chooses where code runs.
      </p>
      {d.error && <p className="error-text small" role="alert">sandbox.json is not valid: {d.error}. Saving here replaces it.</p>}

      <h3>Limits</h3>
      <div className="row gap wrap">
        <label>Time limit per call (seconds)
          <input type="number" min={1} max={900} value={timeout} disabled={!canBuild} onChange={(e) => setTimeoutS(Number(e.target.value))} />
        </label>
        <label>Memory (MB)
          <input type="number" min={64} max={16384} step={64} value={memory} disabled={!canBuild} onChange={(e) => setMemory(Number(e.target.value))} />
        </label>
      </div>

      <h3>Network</h3>
      {d.enforces_egress ? (
        <>
          <p className="muted small">
            Code has no network unless you list hosts here; it then reaches those hosts and nothing else. A host covers
            ports 443 and 80; add <code>:port</code> for another, or <code>*.example.com</code> for its subdomains.
          </p>
          {hosts.length === 0 && <p className="empty-state">No network: code cannot reach any host.</p>}
          {hosts.length > 0 && (
            <ul className="chip-list" aria-label="allowed hosts">
              {hosts.map((h) => (
                <li key={h}><code>{h}</code>{canBuild && (
                  <button type="button" className="link small" aria-label={`remove ${h}`} onClick={() => setHosts(hosts.filter((x) => x !== h))}>×</button>
                )}</li>
              ))}
            </ul>
          )}
          {canBuild && (
            <form className="row gap wrap" onSubmit={(e) => { e.preventDefault(); add(); }}>
              <input className="mono" aria-label="host to allow" placeholder="api.github.com" value={draft} onChange={(e) => setDraft(e.target.value)} />
              <button type="submit" disabled={!draft.trim()}>Allow host</button>
            </form>
          )}
        </>
      ) : (
        <div className={d.refuses_egress ? "notice error" : "notice"} data-testid="egress-not-enforced">
          <p>
            This server cannot limit which hosts code reaches ({d.mode} mode), so network access is not offered.
            {d.mode === "inprocess" ? " Code here can reach whatever the server can." : " Code here has no network."}
            {" "}An operator enables it with <code>AGENTCANVAS_SCRIPT_EXECUTION=docker</code> or <code>remote</code>.
          </p>
          {hosts.length > 0 && (
            <p>
              sandbox.json lists {hosts.map((h) => <code key={h}>{h}</code>).reduce<React.ReactNode[]>((a, c, i) => (i ? [...a, ", ", c] : [c]), [])}.
              {d.refuses_egress ? " This server refuses to run code while the list is there." : " It is ignored here."}
              {canBuild && <> <button type="button" className="link small" onClick={() => setHosts([])}>Remove the list</button></>}
            </p>
          )}
        </div>
      )}
      {inject.length > 0 && !d.applies_inject && (
        <p className="warn-text small">sandbox.json has {inject.length} header rule(s); only the LangSmith sandbox applies them, so requests here go out without those headers.</p>
      )}
      {d.problems && d.problems.length > 0 && (
        <ul className="small" aria-label="sandbox problems">
          {d.problems.map((p) => <li key={`${p.code}-${p.message}`} className={p.code.startsWith("E") ? "error-text" : "warn-text"}>{p.code}: {p.message}</li>)}
        </ul>
      )}
      {canBuild ? (
        <div className="row gap">
          <button type="button" className="primary" disabled={save.isPending} onClick={() => save.mutate()}>Save sandbox settings</button>
        </div>
      ) : <p className="muted small">Your role can see these settings; a builder or admin can change them.</p>}
      {save.error && <p className="error-text small" role="alert">Could not save: {(save.error as Error).message}</p>}
    </section>
  );
}
