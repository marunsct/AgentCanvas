/** Connect / Connected for an app connector that signs in (G-7): shown under an App connector block whose settings carry
 * `oauth`. "Acts as" decides whose connection a run uses; the panel shows that connection and connects it. */
import { useState } from "react";
import type { IRResource } from "../ir/model";
import { useCanOrg } from "../api/session";
import { connect, connectionFor, resourceOf, useConnections } from "../settings/connections";

type Identity = "starter" | "service";

export function ConnectPanel({ resource }: { resource: IRResource }) {
  const cfg = (resource.config ?? {}) as { url?: string; oauth?: { identity?: Identity } };
  const identity: Identity = cfg.oauth?.identity === "service" ? "service" : "starter";
  const admin = useCanOrg("admin");
  const list = useConnections();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const url = resourceOf(cfg.url);
  const have = connectionFor(list.data, cfg.url, identity);
  const go = async () => {
    if (!url) return;
    setBusy(true);
    setError(null);
    try { if (!(await connect(url, identity === "service"))) setBusy(false); } catch (e) { setError((e as Error).message); setBusy(false); }
  };
  const mayConnect = identity === "starter" || admin;
  return (
    <div className="connect-panel" aria-label="Connection">
      <p className="small">
        {identity === "starter"
          ? "Runs use the connection of the person who starts them."
          : "Runs with no person (schedules, webhooks, deployments) and admins' runs use the organisation's service connection; a builder's draft run cannot."}
      </p>
      {!url && <p className="muted small">Enter the server URL to connect.</p>}
      {url && (have && have.status === "active"
        ? <p className="small"><span className="badge ok">connected</span> {identity === "service" ? "as the service account" : "as you"} to <code>{have.host}</code></p>
        : <p className="small"><span className="badge warn">{have ? "expired" : "not connected"}</span>{" "}
            {identity === "starter" ? "You have not connected this app yet." : "The service connection is not connected."}</p>)}
      {url && mayConnect && (
        <button type="button" onClick={() => void go()} disabled={busy}>
          {busy ? "Opening the sign-in…" : have ? "Connect again" : identity === "service" ? "Connect the service account" : "Connect"}
        </button>
      )}
      {url && !mayConnect && <p className="muted small">An admin connects the service account.</p>}
      {error && <p className="error-text small" role="alert">{error}</p>}
    </div>
  );
}
