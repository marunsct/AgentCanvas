/** Settings › Organisation › General: the organisation's name, how long its conversations are kept and where it
 * publishes (the limits it may set for itself are OrgLimits). Admins change; everyone else reads. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { apiFetch } from "../api/client";
import type { OrgSettingsOut } from "../api/generated";
import { useCan } from "../api/session";
import { toast } from "../ui/dialog";
import { QueryStatus } from "../ui/QueryStatus";
import { SsoGroups } from "./SsoGroups";

const days = (n: number) => (n === 0 ? "kept forever" : `${n} days`);

export function OrgSettings() {
  const qc = useQueryClient();
  const admin = useCan("admin");
  const q = useQuery({ queryKey: ["org-settings"], queryFn: () => apiFetch<OrgSettingsOut>("/org/settings") });
  const [name, setName] = useState("");
  const [mode, setMode] = useState<"server" | "days" | "forever">("server");
  const [n, setN] = useState(90);
  const [envs, setEnvs] = useState<string[]>([]);
  const [newEnv, setNewEnv] = useState("");
  useEffect(() => {
    if (!q.data) return;
    setName(q.data.name);
    const r = q.data.retention_days;
    setMode(r == null ? "server" : r === 0 ? "forever" : "days");
    setN(r || q.data.effective_retention_days || 90);
    setEnvs(q.data.offered_environments);
  }, [q.data]);
  const save = useMutation({
    mutationFn: () => apiFetch<OrgSettingsOut>("/org/settings", {
      method: "PUT",
      json: {
        ...(q.data?.name_editable && name.trim() !== q.data.name ? { name: name.trim() } : {}),
        retention_days: mode === "server" ? null : mode === "forever" ? 0 : n,
        // only when changed: an organisation without a list publishes anywhere, and saving retention must not change that
        ...(JSON.stringify(envs) !== JSON.stringify(q.data?.offered_environments) ? { environments: envs } : {}),
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

      <h3>Environments</h3>
      <p className="muted small">Where workflows are published. Publishing to a name not on the list is refused.</p>
      <ul className="chip-list" aria-label="environments">
        {envs.map((e) => (
          <li key={e}><code>{e}</code>{admin && envs.length > 1 && (
            <button type="button" className="link small" aria-label={`remove ${e}`} onClick={() => setEnvs(envs.filter((x) => x !== e))}>×</button>
          )}</li>
        ))}
      </ul>
      {admin && (
        <div className="row gap">
          <input aria-label="new environment" placeholder="e.g. qa" value={newEnv} pattern="[a-z][a-z0-9-]{0,30}" style={{ width: 160 }}
            onChange={(e) => setNewEnv(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ""))} />
          <button type="button" disabled={!/^[a-z][a-z0-9-]{0,30}$/.test(newEnv) || envs.includes(newEnv)}
            onClick={() => { setEnvs([...envs, newEnv]); setNewEnv(""); }}>Add</button>
        </div>
      )}

      <SsoGroups data={d} admin={admin} />

      {admin
        ? <button type="button" className="primary" disabled={save.isPending || (mode === "days" && !(n >= 1))} onClick={() => save.mutate()}>Save</button>
        : <p className="muted small">Only an admin can change the organisation's settings.</p>}
      {save.error && <p className="error-text small" role="alert">Could not save: {(save.error as Error).message}</p>}
    </section>
  );
}
