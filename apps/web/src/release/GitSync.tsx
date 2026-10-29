/** G-14: the Git round trip. A workflow connected to a branch of a GitHub repository opens (or updates) a pull request
 * with its export and the diff of what changed, and imports the branch back into the draft. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError, apiFetch } from "../api/client";
import { can } from "../api/session";
import { useEditor } from "../editor/store";
import type { GitLinkOut, PullOut, PushOut } from "../api/generated";

const AUTO = [
  ["publish", "on every publish"],
  ["version", "on every saved version"],
  ["off", "only when I ask"],
] as const;

export function GitSync({ wfId, version, target }: { wfId: string; version: string; target: string }) {
  const qc = useQueryClient();
  const key = ["git-link", wfId];
  const link = useQuery({ queryKey: key, queryFn: () => apiFetch<GitLinkOut | null>(`/workflows/${wfId}/git`) });
  const [repo, setRepo] = useState("");
  const [branch, setBranch] = useState("agentcanvas/export");
  const [folder, setFolder] = useState("");
  const [auto, setAuto] = useState<"publish" | "version" | "off">("publish");
  const [conflict, setConflict] = useState<{ what: "push" | "pull"; text: string } | null>(null);
  const refresh = () => void qc.invalidateQueries({ queryKey: key });
  const connect = useMutation({
    mutationFn: () => apiFetch<GitLinkOut>(`/workflows/${wfId}/git`, {
      method: "PUT", json: { repo: repo.trim(), branch: branch.trim(), folder: folder.trim(), auto, target },
    }),
    onSuccess: refresh,
  });
  const disconnect = useMutation({
    mutationFn: () => apiFetch<void>(`/workflows/${wfId}/git`, { method: "DELETE" }),
    onSuccess: refresh,
  });
  const push = useMutation({
    mutationFn: (overwrite: boolean) => apiFetch<PushOut>(`/workflows/${wfId}/git/push`, {
      method: "POST", json: { version: version || null, overwrite },
    }),
    onMutate: () => setConflict(null),
    onSuccess: refresh,
    onError: (e) => { refresh(); if (e instanceof ApiError && e.status === 409) setConflict({ what: "push", text: e.message }); },
  });
  const pull = useMutation({
    mutationFn: async (overwrite: boolean) => {
      await useEditor.getState().flush();
      const out = await apiFetch<PullOut>(`/workflows/${wfId}/git/pull`, { method: "POST", json: { overwrite } });
      if (out.outcome === "applied") await useEditor.getState().load(wfId);
      return out;
    },
    onMutate: () => setConflict(null),
    onSuccess: refresh,
    onError: (e) => { if (e instanceof ApiError && e.status === 409) setConflict({ what: "pull", text: e.message }); },
  });
  const data = link.data;
  const failure = [connect, disconnect, push, pull].map((m) => m.error).find(
    (e) => e && !(e instanceof ApiError && e.status === 409),
  ) as Error | undefined;
  const writable = can("builder");
  return (
    <details className="git-sync small" data-testid="git-sync">
      <summary>Git round trip…{data ? ` (${data.repo} · ${data.branch})` : ""}</summary>
      {!data ? (
        <>
          <p className="muted">
            Connect this workflow to a branch of a GitHub repository (not its default branch). A publish then opens a
            pull request with the export, the diff of what changed and a check that runs its tests; a merged one comes
            back with <b>Import from branch</b>. Uses the project secret <code>GITHUB_TOKEN</code>.
          </p>
          <form className="stack" onSubmit={(e) => { e.preventDefault(); connect.mutate(); }}>
            <label htmlFor="git-repo">Repository</label>
            <input id="git-repo" placeholder="owner/name" value={repo} onChange={(e) => setRepo(e.target.value)} required />
            <label htmlFor="git-branch">Branch</label>
            <input id="git-branch" value={branch} onChange={(e) => setBranch(e.target.value)} required />
            <label htmlFor="git-folder">Folder (empty: the repository root)</label>
            <input id="git-folder" placeholder="agents/support" value={folder} onChange={(e) => setFolder(e.target.value)} />
            <label htmlFor="git-auto">Open a pull request</label>
            <select id="git-auto" value={auto} onChange={(e) => setAuto(e.target.value as typeof auto)}>
              {AUTO.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
            </select>
            <button type="submit" className="primary" data-testid="git-connect" disabled={!writable || connect.isPending || !repo.trim()}>
              {connect.isPending ? "Connecting…" : "Connect"}</button>
          </form>
        </>
      ) : (
        <div className="stack">
          <p>
            <code>{data.repo}</code> branch <code>{data.branch}</code>{data.folder ? <> in <code>{data.folder}</code></> : null};
            pull requests {AUTO.find(([id]) => id === data.auto)?.[1] ?? data.auto}.
            {data.pr_url && <> <a href={data.pr_url} target="_blank" rel="noreferrer" data-testid="git-pr">Pull request #{data.pr_number}</a></>}
            {data.last_version && <span className="muted"> Last sent: {data.last_version}.</span>}
          </p>
          {data.last_error && <p className="error-text" role="alert" data-testid="git-last-error">Last sync failed: {data.last_error}</p>}
          <div className="row gap">
            <button type="button" className="primary" data-testid="git-push" disabled={!writable || push.isPending}
              onClick={() => push.mutate(false)}>
              {push.isPending ? "Sending…" : `Open pull request for ${version ? `v${version}` : "the draft"}`}</button>
            <button type="button" data-testid="git-pull" disabled={!writable || pull.isPending} onClick={() => pull.mutate(false)}>
              {pull.isPending ? "Importing…" : "Import from branch"}</button>
            <button type="button" className="link" disabled={!writable || disconnect.isPending} onClick={() => disconnect.mutate()}>
              Disconnect</button>
          </div>
          {conflict && (
            <div className="error-text" role="alert" data-testid="git-conflict">
              <p>{conflict.text}</p>
              <button type="button" onClick={() => (conflict.what === "push" ? push : pull).mutate(true)}>
                {conflict.what === "push" ? "Push anyway (replace the branch's changes)" : "Import anyway (replace the draft)"}</button>
            </div>
          )}
          {push.data && (
            <p data-testid="git-pushed">
              {push.data.pr_url
                ? <>{push.data.pr_created ? "Opened" : "Updated"} <a href={push.data.pr_url} target="_blank" rel="noreferrer">pull request #{push.data.pr_number}</a>
                  {push.data.changed ? "" : " (the branch already held this export)"}.</>
                : push.data.note}
            </p>
          )}
          {pull.data && <p data-testid="git-pulled">{pull.data.outcome === "applied" ? "Imported into the draft: " : ""}{pull.data.summary}.</p>}
        </div>
      )}
      {failure && <p className="error-text" role="alert">{failure.message}</p>}
    </details>
  );
}
