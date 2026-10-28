/** Versions (M6-T1): save the draft as a version, compare a version with the draft on the canvas, restore it. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { apiDownload, apiFetch } from "../api/client";
import type { DiffOut, ElementDiff, VersionOut } from "../api/generated";
import { can } from "../api/session";
import { useEditor } from "../editor/store";
import { useUi } from "../editor/ui";
import { ContractSection } from "./ContractSection";
import { AgentSpecExport } from "./AgentSpec";
import { ExportSection } from "./ExportSection";
import { PublishSection } from "./PublishSection";
import { TriggersSection } from "./TriggersSection";
import { QueryStatus } from "../ui/QueryStatus";
import { confirmDialog } from "../ui/dialog";

export function VersionsPanel() {
  const wf = useEditor((s) => s.wf);
  const qc = useQueryClient();
  const [message, setMessage] = useState("");
  const [diff, setDiff] = useState<DiffOut | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const download = (path: string, name: string) => {
    setDownloadError(null);
    apiDownload(path, name).catch((e: Error) => setDownloadError(e.message));
  };
  const uiDiff = useUi((s) => s.diff);
  const key = ["versions", wf?.id];
  const versions = useQuery({ queryKey: key, enabled: Boolean(wf), queryFn: () => apiFetch<VersionOut[]>(`/workflows/${wf!.id}/versions`) });
  const save = useMutation({
    mutationFn: async () => {
      await useEditor.getState().flush();
      return apiFetch<VersionOut>(`/workflows/${wf!.id}/versions`, { method: "POST", json: { message } });
    },
    onSuccess: () => { setMessage(""); void qc.invalidateQueries({ queryKey: key }); },
  });
  const compare = async (v: VersionOut) => {
    await useEditor.getState().flush();
    const d = await apiFetch<DiffOut>(`/workflows/${wf!.id}/versions/${v.id}/diff/draft`);
    setDiff(d);
    const ids = (e?: ElementDiff) => ({ added: e?.added ?? [], changed: (e?.changed ?? []).map((c) => c.id) });
    const n = ids(d.nodes), r = ids(d.resources);
    useUi.getState().setDiff({ against: v.semver, added: [...n.added, ...r.added], changed: [...n.changed, ...r.changed] });
  };
  const clear = () => { setDiff(null); useUi.getState().setDiff(null); };
  const restore = async (v: VersionOut) => {
    if (!(await confirmDialog({ title: `Replace the draft with v${v.semver}?`, body: "Undo is not available; save a version first.", confirm: "Replace the draft", danger: true }))) return;
    const { flush, version, load } = useEditor.getState();
    await flush();
    await apiFetch(`/workflows/${wf!.id}/versions/${v.id}/restore`, { method: "POST", version: useEditor.getState().version || version });
    clear();
    await load(wf!.id);
  };
  if (!wf) return null;
  return (
    <div className="panel-body versions-panel">
      <h3>Versions</h3>
      {can("builder") && <form className="row gap" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <input placeholder="What changed?" value={message} onChange={(e) => setMessage(e.target.value)} aria-label="version message" />
        <button type="submit" className="primary" disabled={save.isPending}>Save version</button>
      </form>}
      {save.error && <p className="error-text small">{(save.error as Error).message}</p>}
      <ExportSection wfId={wf.id} versions={versions.data ?? []} />
      <AgentSpecExport wfId={wf.id} name={wf.name} />
      {downloadError && <p className="error-text small" role="alert">{downloadError}</p>}
      {diff && (
        <div className="diff-box" data-testid="diff-summary">
          <div className="row gap"><b>Draft vs v{uiDiff?.against}</b>: {diff.summary}
            <span className="spacer" /><button type="button" className="link small" onClick={clear}>clear</button></div>
          <ul className="small">
            {(diff.nodes?.changed ?? []).map((c) => (
              <li key={c.id}><code>{c.name ?? c.id}</code> {(c.fields ?? []).map((f) => f.path).join(", ")}</li>
            ))}
            {(diff.nodes?.removed ?? []).map((id) => <li key={id} className="muted">removed <code>{id}</code></li>)}
          </ul>
        </div>
      )}
      <QueryStatus q={versions} what="versions" />
      <ul className="version-list">
        {(versions.data ?? []).map((v) => (
          <li key={v.id} data-testid={`version-${v.semver}`}>
            <div className="row gap">
              <b>v{v.semver}</b><span className="muted small">{new Date(v.created_at).toLocaleString()}</span>
              <span className="spacer" />
              <button type="button" className="small" onClick={() => void compare(v)}>Compare</button>
              {can("builder") && <button type="button" className="small" onClick={() => void restore(v)}>Restore</button>}
              <button type="button" className="link small"
                onClick={() => download(`/workflows/${wf.id}/export.zip?version=${v.semver}`, `export-${v.semver}.zip`)}>
                ⬇ zip</button>
            </div>
            {v.message && <p className="small">{v.message}</p>}
          </li>
        ))}
      </ul>
      {versions.data?.length === 0 && <p className="muted">No versions yet. A version freezes the workflow and its workspace code.</p>}
      <ContractSection wfId={wf.id} against={uiDiff?.against ?? null} />
      <PublishSection wfId={wf.id} />
      <TriggersSection wfId={wf.id} />
    </div>
  );
}
