/** Settings › Organisation › General: the organisation's name and how long its conversations are kept; the server's
 * other retentions are shown for reference (an operator sets them). Admins change; everyone else reads. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { apiFetch } from "../api/client";
import type { OrgSettingsOut } from "../api/generated";
import { useCan } from "../api/session";
import { toast } from "../ui/dialog";
import { QueryStatus } from "../ui/QueryStatus";

const days = (n: number) => (n === 0 ? "kept forever" : `${n} days`);

export function OrgSettings() {
  const qc = useQueryClient();
  const admin = useCan("admin");
  const q = useQuery({ queryKey: ["org-settings"], queryFn: () => apiFetch<OrgSettingsOut>("/org/settings") });
  const [name, setName] = useState("");
  const [mode, setMode] = useState<"server" | "days" | "forever">("server");
  const [n, setN] = useState(90);
  useEffect(() => {
    if (!q.data) return;
    setName(q.data.name);
    const r = q.data.retention_days;
    setMode(r == null ? "server" : r === 0 ? "forever" : "days");
    setN(r || q.data.effective_retention_days || 90);
  }, [q.data]);
  const save = useMutation({
    mutationFn: () => apiFetch<OrgSettingsOut>("/org/settings", {
      method: "PUT",
      json: {
        ...(q.data?.name_editable && name.trim() !== q.data.name ? { name: name.trim() } : {}),
        retention_days: mode === "server" ? null : mode === "forever" ? 0 : n,
      },
    }),
    onSuccess: (out) => { qc.setQueryData(["org-settings"], out); toast("Saved the organisation settings"); },
  });
  if (!q.data) return <section className="settings-card"><h2>General</h2><QueryStatus q={q} what="the organisation settings" /></section>;
  const d = q.data;
  return (
    <section className="settings-card" aria-label="Organisation">
      <h2>General</h2>
      <label>Name
        <input aria-label="organisation name" value={name} maxLength={100} disabled={!admin || !d.name_editable}
          onChange={(e) => setName(e.target.value)} />
      </label>
      {!d.name_editable && <p className="muted small">The organisation is named in Matura.</p>}

      <h3>Conversations</h3>
      <p className="muted small">How long a conversation (its messages, checkpoints and runs) is kept after its last activity.</p>
      <div className="radio-list" role="radiogroup" aria-label="conversation retention">
        <label className="row"><input type="radio" name="retention" checked={mode === "server"} disabled={!admin} onChange={() => setMode("server")} />
          The server's default{d.retention_days == null ? ` (${days(d.effective_retention_days)})` : ""}</label>
        <label className="row"><input type="radio" name="retention" checked={mode === "days"} disabled={!admin} onChange={() => setMode("days")} />
          <input type="number" min={1} max={3650} value={n} aria-label="retention days" disabled={!admin || mode !== "days"}
            style={{ width: 90 }} onChange={(e) => setN(Number(e.target.value))} /> days</label>
        <label className="row"><input type="radio" name="retention" checked={mode === "forever"} disabled={!admin} onChange={() => setMode("forever")} />
          Forever</label>
      </div>

      <h3>Set by the server</h3>
      <p className="muted small">
        The audit log keeps entries {days(d.audit_retention_days)}; long-term memories not updated are {d.memory_retention_days === 0
          ? "kept forever" : `deleted after ${d.memory_retention_days} days`}. An operator changes these with
        {" "}<code>AGENTCANVAS_AUDIT_RETENTION_DAYS</code> and <code>AGENTCANVAS_MEMORY_RETENTION_DAYS</code>.
      </p>
      {admin
        ? <button type="button" className="primary" disabled={save.isPending || (mode === "days" && !(n >= 1))} onClick={() => save.mutate()}>Save</button>
        : <p className="muted small">Only an admin can change the organisation's settings.</p>}
      {save.error && <p className="error-text small" role="alert">Could not save: {(save.error as Error).message}</p>}
    </section>
  );
}
