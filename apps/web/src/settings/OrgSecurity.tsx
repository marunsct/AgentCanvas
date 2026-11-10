/** Settings › Organisation › Security (E-8): the networks the organisation's requests may come from, and whether its
 * members must sign in with multi-factor authentication. The list covers every credential acting in the organisation
 * (sign-ins, API tokens, deployment keys, share links, SCIM); the server refuses a list that leaves out the admin
 * saving it, and requiring MFA from an admin whose own sign-in is not. Admins change them. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { apiFetch } from "../api/client";
import type { OrgSettingsOut } from "../api/generated";
import { useCan } from "../api/session";
import { toast } from "../ui/dialog";
import { QueryStatus } from "../ui/QueryStatus";

// narrow local type until the generated API types are regenerated (OrgSettingsOut.security)
export interface OrgSecurityOut {
  allowed_networks: string[];
  your_address: string | null;
  networks_ignored: boolean;
  require_mfa: boolean;
  mfa_available: boolean;
  your_sign_in_is_mfa: boolean;
}
type WithSecurity = OrgSettingsOut & { security?: OrgSecurityOut };

/** The lines of the text box as list entries: trimmed, blank lines dropped. */
export function networkLines(text: string): string[] {
  return text.split(/[\n,]/).map((l) => l.trim()).filter(Boolean);
}

export function OrgSecurity() {
  const qc = useQueryClient();
  const admin = useCan("admin");
  const q = useQuery({ queryKey: ["org-settings"], queryFn: () => apiFetch<WithSecurity>("/org/settings") });
  const sec = q.data?.security;
  const [text, setText] = useState("");
  const [requireMfa, setRequireMfa] = useState(false);
  useEffect(() => { if (sec) { setText(sec.allowed_networks.join("\n")); setRequireMfa(sec.require_mfa); } }, [sec]);
  const save = useMutation({
    mutationFn: () => apiFetch<WithSecurity>("/org/settings", {
      method: "PUT",
      // only what changed: switching MFA needs a real sign-in, and saving the list must not depend on that
      json: { security: { allowed_networks: networkLines(text), ...(requireMfa !== sec?.require_mfa ? { require_mfa: requireMfa } : {}) } },
    }),
    onSuccess: (out) => { qc.setQueryData(["org-settings"], out); toast("Saved the security settings"); },
  });
  if (!sec) return <section className="settings-card"><h2>Security</h2><QueryStatus q={q} what="the security settings" /></section>;
  return (
    <section className="settings-card" aria-label="Security">
      <h2>Security</h2>
      <h3>Allowed networks</h3>
      <p className="muted small">
        Requests acting in this organisation are accepted only from these networks: people signed in, API tokens,
        deployment keys, share links and SCIM. One address (<code>203.0.113.7</code>) or network
        (<code>203.0.113.0/24</code>) per line; leave it empty to accept any address. Webhook triggers are not covered.
      </p>
      {sec.networks_ignored && (
        <p className="callout small" role="status">The server's operator has switched allowed networks off for every organisation (an emergency setting): the list is not applied now.</p>
      )}
      <textarea aria-label="allowed networks" data-testid="allowed-networks" rows={5} value={text} disabled={!admin}
        placeholder="any address" onChange={(e) => setText(e.target.value)} />
      <p className="muted small">
        Your address: <code>{sec.your_address ?? "unknown"}</code>. It must be on the list, so saving cannot lock you out.
      </p>
      <h3>Multi-factor sign-in</h3>
      <label className="row">
        <input type="checkbox" data-testid="require-mfa" checked={requireMfa} onChange={(e) => setRequireMfa(e.target.checked)}
          disabled={!admin || !sec.mfa_available || (!sec.require_mfa && !sec.your_sign_in_is_mfa)} />
        Members must sign in with a second factor
      </label>
      <p className="muted small">
        {!sec.mfa_available
          ? "Needs sign-in through an identity provider; this server has none."
          : sec.your_sign_in_is_mfa
            ? "A member whose sign-in has no second factor is sent back to the identity provider to add one. API tokens are not affected."
            : "To switch it on, sign in with a second factor first: your current sign-in does not count as multi-factor here, so requiring it would lock you out."}
      </p>
      {admin
        ? <button type="button" className="primary" disabled={save.isPending} onClick={() => save.mutate()}>Save</button>
        : <p className="muted small">Only an admin can change the organisation's security settings.</p>}
      {save.error && <p className="error-text small" role="alert">Could not save: {(save.error as Error).message}</p>}
    </section>
  );
}
