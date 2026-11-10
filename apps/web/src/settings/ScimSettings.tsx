/** Settings › Organisation › General › Provisioning (G-13): the SCIM token the identity provider uses to keep members
 * and groups in step with the directory. Shown once when made; admins only. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { apiFetch } from "../api/client";
import type { ScimCreated, ScimStatus } from "../api/generated";
import { useCanOrg } from "../api/session";
import { confirmDialog, toast } from "../ui/dialog";
import { ago } from "../ui/time";

/** The SCIM base URL as the identity provider reaches it: the API sits under `/api` behind the web container. */
export function scimBaseUrl(origin: string, basePath: string): string {
  return `${origin.replace(/\/$/, "")}/api${basePath}`;
}

export function ScimSettings() {
  const qc = useQueryClient();
  const admin = useCanOrg("admin");
  const q = useQuery({ queryKey: ["scim"], queryFn: () => apiFetch<ScimStatus>("/org/scim"), enabled: admin });
  const [made, setMade] = useState<ScimCreated | null>(null);
  const create = useMutation({
    mutationFn: () => apiFetch<ScimCreated>("/org/scim/token", { method: "POST" }),
    onSuccess: (t) => { setMade(t); void qc.invalidateQueries({ queryKey: ["scim"] }); },
  });
  const remove = useMutation({
    mutationFn: () => apiFetch("/org/scim/token", { method: "DELETE" }),
    onSuccess: () => { setMade(null); toast("Provisioning switched off"); void qc.invalidateQueries({ queryKey: ["scim"] }); },
  });
  if (!admin || !q.data?.available) return null;
  const d = q.data;
  return (
    <>
      <h3>Provisioning (SCIM)</h3>
      <p className="muted small">
        Lets your identity provider (Okta, Entra ID, OneLogin…) add, suspend and remove members and push groups. Point it at{" "}
        <code>{scimBaseUrl(window.location.origin, d.base_path ?? "/scim/v2")}</code> with the token below as a bearer token. A group's role is looked up
        by its name in the groups table above.
      </p>
      {made && (
        <div className="callout" role="status" aria-label="new SCIM token">
          <p><strong>Copy the SCIM token now.</strong> It is not shown again.</p>
          <div className="row gap">
            <code className="mono" data-testid="new-scim-token">{made.token}</code>
            <button type="button" onClick={() => void navigator.clipboard?.writeText(made.token).then(() => toast("Copied"))}>Copy</button>
            <button type="button" className="link small" onClick={() => setMade(null)}>Done</button>
          </div>
        </div>
      )}
      <p className="small">
        {d.enabled
          ? <>On. Token <code>{d.prefix}…</code>{d.created_at ? `, made ${ago(d.created_at)}` : ""}.</>
          : "Off. Nobody can provision members over SCIM."}
      </p>
      <div className="row gap">
        <button type="button" disabled={create.isPending}
          onClick={async () => {
            if (!d.enabled || await confirmDialog({ title: "Replace the SCIM token?", body: "The current one stops working at once; update your identity provider.", confirm: "Replace", danger: true })) create.mutate();
          }}>{d.enabled ? "Replace token" : "Create token"}</button>
        {d.enabled && (
          <button type="button" className="link small" disabled={remove.isPending}
            onClick={async () => {
              if (await confirmDialog({ title: "Switch SCIM off?", body: "Your identity provider can no longer change members or groups.", confirm: "Switch off", danger: true })) remove.mutate();
            }}>Switch off</button>
        )}
      </div>
      {(create.error || remove.error) && <p className="error-text small" role="alert">{((create.error || remove.error) as Error).message}</p>}
    </>
  );
}
