/** Settings › You › Connections (G-7): your sign-ins to app connectors, and the organisation's service connections.
 * Connect from a connector's block in the editor; here you see them and disconnect. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { apiFetch } from "../api/client";
import type { ClientsOut, ConnectionOut } from "../api/generated";
import { useCanOrg } from "../api/session";
import { confirmDialog, toast } from "../ui/dialog";
import { QueryStatus } from "../ui/QueryStatus";
import { ago } from "../ui/time";
import { CONNECTIONS_KEY, useConnections } from "./connections";

const WHOSE = (c: ConnectionOut) => (c.owner === "you" ? "You" : c.owner === "service" ? "Service (the organisation)" : "Another person");

export function ConnectionsSettings() {
  const qc = useQueryClient();
  const admin = useCanOrg("admin");
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
      {admin && <ClientCredentials />}
    </section>
  );
}

const CLIENTS_KEY = ["connection-clients"];

/** Admins: the client id (and secret) made at a provider that has neither client ID metadata documents nor dynamic
 * registration, so people can sign in to its connectors (N-5). */
function ClientCredentials() {
  const qc = useQueryClient();
  const clients = useQuery({ queryKey: CLIENTS_KEY, queryFn: () => apiFetch<ClientsOut>("/connections/clients") });
  const [url, setUrl] = useState("");
  const [id, setId] = useState("");
  const [secret, setSecret] = useState("");
  const save = useMutation({
    mutationFn: () => apiFetch("/connections/clients", { method: "PUT", json: { server_url: url.trim(), client_id: id.trim(), client_secret: secret || null } }),
    onSuccess: () => { toast("Saved"); setUrl(""); setId(""); setSecret(""); void qc.invalidateQueries({ queryKey: CLIENTS_KEY }); },
  });
  const forget = useMutation({
    mutationFn: (issuer: string) => apiFetch(`/connections/clients?issuer=${encodeURIComponent(issuer)}`, { method: "DELETE" }),
    onSuccess: () => { toast("Removed"); void qc.invalidateQueries({ queryKey: CLIENTS_KEY }); },
  });
  return (
    <details>
      <summary>Sign-in credentials for providers that need them (admins)</summary>
      <p className="muted small">
        Most app connectors sign in with no setup. A provider that takes neither client ID metadata documents nor dynamic
        registration needs an app made at the provider: register <code>{clients.data?.redirect_uri ?? "…/oauth/callback"}</code> as its
        redirect address, then enter the client id and secret here with the connector's server address.
      </p>
      <QueryStatus q={clients} what="the credentials" />
      {clients.data && clients.data.clients.length > 0 && (
        <table className="table" aria-label="client credentials">
          <thead><tr><th>Sign-in server</th><th>Client id</th><th>Secret</th><th>For</th><th aria-label="actions" /></tr></thead>
          <tbody>
            {clients.data.clients.map((c) => (
              <tr key={c.issuer}>
                <td title={c.issuer}><code>{c.host}</code></td><td><code>{c.client_id}</code></td><td>{c.has_secret ? "kept" : "none"}</td>
                <td className="small">{c.resources ? c.resources.join(", ") || "no server yet" : "any server (registered by AgentCanvas)"}</td>
                <td className="actions">
                  <button type="button" className="link small" aria-label={`remove the credentials for ${c.host}`} disabled={forget.isPending}
                    onClick={async () => {
                      if (await confirmDialog({ title: `Remove the credentials for ${c.host}?`, body: "People cannot sign in to its connectors until they are entered again.", confirm: "Remove", danger: true })) forget.mutate(c.issuer);
                    }}>Remove</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <form className="row gap wrap" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <input type="url" aria-label="connector server address" placeholder="https://mcp.example.com/mcp" value={url} onChange={(e) => setUrl(e.target.value)} required />
        <input aria-label="client id" placeholder="client id" value={id} onChange={(e) => setId(e.target.value)} required autoComplete="off" />
        <input type="password" aria-label="client secret" placeholder="client secret (if it has one)" value={secret} onChange={(e) => setSecret(e.target.value)} autoComplete="new-password" />
        <button type="submit" disabled={save.isPending}>Save</button>
      </form>
      {(save.error || forget.error) && <p className="error-text small" role="alert">{((save.error || forget.error) as Error).message}</p>}
    </details>
  );
}
