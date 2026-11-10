/** Settings › Organisation › General (admins): the organisation's data (E-7). Export everything as a zip, built in
 * the background and downloaded once through a short-lived link; delete the organisation, suspended at once and
 * deleted for good after a grace period that can be taken back.
 * The API's generated types do not have these routes yet: the narrow types below stand in for them. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiDownload, apiFetch } from "../api/client";
import { useCanOrg, useMe } from "../api/session";
import { confirmDialog, promptDialog, toast } from "../ui/dialog";

export interface OrgExport {
  id: string;
  status: "queued" | "building" | "ready" | "downloaded" | "expired" | "failed";
  created_at: string;
  finished_at: string | null;
  expires_at: string | null;
  size: number | null;
  sha256: string | null;
  error: string | null;
}
export interface Deletion { scheduled: boolean; suspended_at: string | null; delete_after: string | null }

const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : "");
const mb = (n: number | null) => (n == null ? "" : `${(n / 1024 / 1024).toFixed(1)} MB`);

export function OrgData() {
  const admin = useCanOrg("admin");
  const name = useMe((s) => s.me.org_names?.[s.me.org_id]) ?? "";
  const qc = useQueryClient();
  const exports = useQuery({
    queryKey: ["org-exports"], enabled: admin,
    queryFn: () => apiFetch<OrgExport[]>("/org/export"),
    // poll while one is being built
    refetchInterval: (q) => (q.state.data?.some((e) => e.status === "queued" || e.status === "building") ? 3000 : false),
  });
  const deletion = useQuery({ queryKey: ["org-deletion"], enabled: admin, queryFn: () => apiFetch<Deletion>("/org/deletion") });
  const start = useMutation({
    mutationFn: () => apiFetch<OrgExport>("/org/export", { method: "POST" }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["org-exports"] }),
  });
  const download = useMutation({
    mutationFn: (id: string) => apiFetch<{ path: string }>(`/org/export/${id}/link`, { method: "POST" }),
    onSuccess: (link) => {
      window.location.assign(`/api${link.path}`);  // the link needs no sign-in: the browser downloads it itself
      void qc.invalidateQueries({ queryKey: ["org-exports"] });
    },
  });
  const remove = useMutation({
    mutationFn: (body: { confirm: string; reason: string }) => apiFetch<Deletion>("/org", { method: "DELETE", json: body }),
    onSuccess: (d) => { qc.setQueryData(["org-deletion"], d); toast(`The organisation will be deleted on ${when(d.delete_after)}`); },
  });
  const keep = useMutation({
    mutationFn: () => apiFetch<Deletion>("/org/deletion", { method: "DELETE" }),
    onSuccess: (d) => { qc.setQueryData(["org-deletion"], d); toast("The deletion was taken back"); },
  });
  if (!admin) return null;
  const latest = exports.data?.[0];
  const busy = latest && (latest.status === "queued" || latest.status === "building");
  const ask = async () => {
    const typed = await promptDialog({
      title: "Delete the organisation?",
      body: "Everyone loses access at once and every run stops. After the grace period everything is deleted for good: projects, workflows, conversations, memories, files, the audit log. Until then an admin can take it back here. Export the data first if you need it.",
      confirm: "Next", danger: true,
      input: { label: `Type the organisation's name: ${name}`, required: true },
    });
    if (typed == null) return;
    if (typed.trim() !== name) { toast("The name did not match: nothing was deleted", "info"); return; }
    const reason = await promptDialog({
      title: "Why is it deleted?", confirm: "Delete the organisation", danger: true,
      input: { label: "Reason (kept in the audit log)", placeholder: "e.g. contract ended", required: true },
    });
    if (reason && reason.trim()) remove.mutate({ confirm: name, reason: reason.trim() });
  };
  const error = (start.error ?? download.error ?? remove.error ?? keep.error) as Error | null;
  return (
    <section className="settings-card danger-zone" aria-label="The organisation's data">
      <h2>The organisation's data</h2>
      <h3>Export</h3>
      <p className="muted small">
        A zip of everything the organisation holds (no keys or secrets), built in the background. It downloads once and
        is deleted after a day.
      </p>
      <div className="row gap">
        <button type="button" disabled={start.isPending || Boolean(busy)} onClick={() => start.mutate()}>Export everything</button>
        {latest && <span className="small" data-testid="export-status">
          {busy && "Building the export…"}
          {latest.status === "ready" && <>Ready ({mb(latest.size)}), until {when(latest.expires_at)}{" "}
            <button type="button" className="link" disabled={download.isPending} onClick={() => download.mutate(latest.id)}>Download</button></>}
          {latest.status === "downloaded" && "Downloaded: export again for another copy"}
          {latest.status === "expired" && "The last export expired"}
          {latest.status === "failed" && <span className="error-text">The export failed: {latest.error}</span>}
        </span>}
      </div>
      <h3>Delete the organisation</h3>
      {deletion.data?.scheduled
        ? <div role="status" data-testid="deletion-scheduled">
            <p>Suspended: nobody can use it. It will be deleted for good on <b>{when(deletion.data.delete_after)}</b>.</p>
            <button type="button" disabled={keep.isPending} onClick={async () => {
              if (await confirmDialog({ title: "Take the deletion back?", body: "The organisation opens again as it was. Runs that were stopped stay stopped.", confirm: "Take it back" })) keep.mutate();
            }}>Take the deletion back</button>
          </div>
        : <>
            <p className="muted small">Suspended at once, deleted for good after a grace period (7 days unless your operator set another).</p>
            <button type="button" className="danger" disabled={remove.isPending} onClick={ask}>Delete the organisation…</button>
          </>}
      {error && <p role="alert" className="error-text small">{error.message}</p>}
    </section>
  );
}

/** Settings › You › Your data: download what the organisation holds about you (GDPR Art. 15 and 20). */
export function MyData() {
  const save = useMutation({
    mutationFn: () => apiDownload("/me/export", "agentcanvas-my-data.json"),
  });
  return (
    <section className="settings-card" aria-label="Your data">
      <h2>Your data</h2>
      <p className="muted small">
        Your account and memberships, and in this organisation your API tokens (never the secret), sign-ins, Copilot
        chats, the conversations you started, your decisions, memories, files and audit entries, as one JSON file.
        Downloading it is recorded in the audit log.
      </p>
      <button type="button" disabled={save.isPending} onClick={() => save.mutate()}>Download my data</button>
      {save.isError && <p role="alert" className="error-text small">{(save.error as Error).message}</p>}
    </section>
  );
}
