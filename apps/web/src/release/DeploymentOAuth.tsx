/** OAuth sign-in for a deployment's MCP server, A2A agent, AG-UI endpoint and deployment API (E-11): people sign in
 * with the organisation's identity provider instead of sharing a key, and each is their own end user. Off until a builder turns it on; the keys keep
 * working beside it. Builders only. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { apiFetch } from "../api/client";
import type { DeploymentOut, OAuthOut } from "../api/generated";

export const DEFAULT_SCOPE = "agentcanvas:deployment:run";

export function DeploymentOAuth({ deployment }: { deployment: DeploymentOut }) {
  const qc = useQueryClient();
  const queryKey = ["deployment-oauth", deployment.id];
  const oauth = useQuery({ queryKey, queryFn: () => apiFetch<OAuthOut>(`/deployments/${deployment.id}/oauth`) });
  const [issuer, setIssuer] = useState("");
  const [audience, setAudience] = useState("");
  const [scopes, setScopes] = useState(DEFAULT_SCOPE);
  const [note, setNote] = useState<string | null>(null);
  const seeded = useRef<string | null>(null); // the deployment the form was filled from
  const seed = (d: OAuthOut) => {
    setIssuer(d.issuer ?? "");
    setAudience(d.audience ?? "");
    setScopes(d.scopes.join(" "));
  };
  useEffect(() => {
    if (!oauth.data || seeded.current === deployment.id) return;
    seeded.current = deployment.id;
    seed(oauth.data);
  }, [oauth.data, deployment.id]);
  const save = useMutation({
    mutationFn: (enabled: boolean) => apiFetch<OAuthOut>(`/deployments/${deployment.id}/oauth`, {
      method: "PUT",
      json: enabled
        ? { enabled, issuer: issuer.trim() || null, audience: audience.trim() || null, scopes: scopes.split(/\s+/).filter(Boolean) }
        : { enabled },
    }),
    onSuccess: (out) => {
      qc.setQueryData(queryKey, out);
      seed(out);
      void qc.invalidateQueries({ queryKey: ["deployments"] });
      setNote(out.enabled ? "Saved. People can now sign in to this deployment's endpoints and API." : "Turned off: only the keys work.");
    },
  });
  const data = oauth.data;
  const id = `oauth-${deployment.id}`;
  const error = (oauth.error ?? save.error) as Error | null;
  return (
    <details className="connect small" data-testid={`oauth-${deployment.environment}`}>
      <summary>Sign-in with OAuth{data?.enabled ? ": on" : ""}</summary>
      <p>
        Let people sign in to this deployment&apos;s MCP server, A2A agent, AG-UI endpoint and API with your identity
        provider instead of sharing a key. Clients that follow the MCP sign-in standard (Claude, ChatGPT, VS Code) then
        need only the address, and each person has their own memories, conversations and runs. The keys keep working
        beside it.
      </p>
      {error && <p className="error-text" role="alert">{error.message}</p>}
      {data && (
        <form onSubmit={(e) => { e.preventDefault(); save.mutate(true); }}>
          <label htmlFor={`${id}-issuer`}>Issuer (authorization server)</label>
          <input id={`${id}-issuer`} value={issuer} maxLength={2000} data-testid="oauth-issuer"
                 placeholder={data.authorization_server && !data.issuer ? data.authorization_server : "https://login.example.com/…"}
                 onChange={(e) => setIssuer(e.target.value)} />
          <p className="muted">
            {data.authorization_server && !issuer.trim()
              ? <>Empty: the identity provider people sign in to AgentCanvas with ({data.authorization_server}).</>
              : "The issuer (iss) of the access tokens; it must publish its signing keys."}
          </p>
          <label htmlFor={`${id}-audience`}>Audience (optional)</label>
          <input id={`${id}-audience`} value={audience} maxLength={2000} placeholder="api://agentcanvas"
                 data-testid="oauth-audience" onChange={(e) => setAudience(e.target.value)} />
          <p className="muted">
            Tokens issued for the endpoint&apos;s address are always accepted. Set this when your identity provider
            issues tokens for a fixed audience instead (Microsoft Entra ID, Okta).
          </p>
          <label htmlFor={`${id}-scopes`}>Required scopes (separated by spaces; empty for none)</label>
          <input id={`${id}-scopes`} value={scopes} data-testid="oauth-scopes" onChange={(e) => setScopes(e.target.value)} />
          <div className="row gap">
            <button type="submit" className="small" disabled={save.isPending}>{data.enabled ? "Save" : "Turn on"}</button>
            {data.enabled && (
              <button type="button" className="small danger" disabled={save.isPending} onClick={() => save.mutate(false)}>Turn off</button>
            )}
          </div>
          {data.enabled && (
            <>
              <p className="muted">Register these with your identity provider as the API (resource) the tokens are for:</p>
              <p id={`${id}-mcp`} className="connect-label">MCP server (resource)</p>
              <code className="mono" aria-labelledby={`${id}-mcp`} data-testid="oauth-mcp-resource">{data.mcp_resource}</code>
              <p id={`${id}-a2a`} className="connect-label">A2A agent (resource)</p>
              <code className="mono" aria-labelledby={`${id}-a2a`}>{data.a2a_resource}</code>
              <p id={`${id}-agui`} className="connect-label">AG-UI endpoint (resource)</p>
              <code className="mono" aria-labelledby={`${id}-agui`} data-testid="oauth-ag-ui-resource">{data.ag_ui_resource}</code>
              <p id={`${id}-api`} className="connect-label">Deployment API: runs/wait and the others (resource)</p>
              <code className="mono" aria-labelledby={`${id}-api`} data-testid="oauth-api-resource">{data.api_resource}</code>
              <p id={`${id}-meta`} className="connect-label">Where clients find the issuer (RFC 9728)</p>
              <code className="mono" aria-labelledby={`${id}-meta`}>{data.mcp_metadata}</code>
            </>
          )}
          {note && <p className="muted" role="status">{note}</p>}
        </form>
      )}
    </details>
  );
}
