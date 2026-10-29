/** Settings › You › Connections (G-7): your sign-ins to app connectors, and the organisation's service connections.
 * Connect from a connector's block in the editor; here you see them and disconnect. */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import type { ConnectionOut } from "../api/generated";
import { useCan } from "../api/session";
import { confirmDialog, toast } from "../ui/dialog";
import { QueryStatus } from "../ui/QueryStatus";
import { ago } from "../ui/time";
import { CONNECTIONS_KEY, useConnections } from "./connections";

const WHOSE = (c: ConnectionOut) => (c.owner === "you" ? "You" : c.owner === "service" ? "Service (the organisation)" : "Another person");

export function ConnectionsSettings() {
  const qc = useQueryClient();
  const admin = useCan("admin");
  const list = useConnections();
  const remove = useMutation({
    mutationFn: (id: string) => apiFetch(`/connections/${encodeURIComponent(id)}`, { method: "DELETE" }),
    onSuccess: () => { toast("Disconnected"); void qc.invalidateQueries({ queryKey: CONNECTIONS_KEY }); },
  });
  return (
    <section className="settings-card" aria-label="Connections">
      <h2>Connections</h2>
      <p className="muted small">
        App connectors that need a sign-in (Linear, Atlassian…) use your own account when you start a run. Connect one from its
        block in the editor (select the block, then <b>Connect</b>). Schedules, webhooks and deployments have no person: an admin
        connects a service account and sets the block to act as it.
      </p>
      <QueryStatus q={list} what="your connections" />
      {list.data && list.data.length === 0 && <p className="empty-state">No connections yet.</p>}
      {list.data && list.data.length > 0 && (
        <table className="table" aria-label="connections">
          <thead><tr><th>App</th><th>Whose</th><th>Status</th><th>Connected</th><th aria-label="actions" /></tr></thead>
          <tbody>
            {list.data.map((c) => (
              <tr key={c.id} className={c.status === "active" ? "" : "muted"}>
                <td title={c.resource}><code>{c.host}</code></td>
                <td>{WHOSE(c)}</td>
                <td>{c.status === "active" ? "connected" : "expired: connect it again"}</td>
                <td>{ago(c.created_at)}</td>
                <td className="actions">{(c.owner === "you" || admin) && (
                  <button type="button" className="link small" aria-label={`disconnect ${c.host} (${WHOSE(c)})`} disabled={remove.isPending}
                    onClick={async () => {
                      if (await confirmDialog({ title: `Disconnect ${c.host}?`, body: "Runs that use this connection fail until it is connected again.", confirm: "Disconnect", danger: true })) remove.mutate(c.id);
                    }}>Disconnect</button>
                )}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {remove.error && <p className="error-text small" role="alert">Could not disconnect: {(remove.error as Error).message}</p>}
    </section>
  );
}
