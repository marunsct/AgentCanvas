/** Settings › Organisation › General › Limits: server settings the organisation may set for itself. The server's value
 * (an environment variable) is the default and, for local MCP, the rate limits and the audit log, the ceiling. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { apiFetch } from "../api/client";
import type { OrgSettingsOut } from "../api/generated";
import { useCan } from "../api/session";
import { toast } from "../ui/dialog";

type Own = Record<string, number | boolean | null>;

/** `kind`: how the server's value limits the organisation's. */
const ROWS: { key: string; label: string; env: string; unit?: string; kind: "switch" | "lower" | "longer" | "any" }[] = [
  { key: "allow_local_mcp", label: "Local MCP servers", env: "AGENTCANVAS_ALLOW_LOCAL_MCP", kind: "switch" },
  { key: "approve_own_runs", label: "Approving your own runs", env: "AGENTCANVAS_APPROVE_OWN_RUNS", kind: "switch" },
  { key: "rate_limit_per_deployment", label: "Calls per deployment", env: "AGENTCANVAS_RATE_LIMIT_PER_DEPLOYMENT", unit: "a minute", kind: "lower" },
  { key: "rate_limit_per_webhook", label: "Calls per webhook", env: "AGENTCANVAS_RATE_LIMIT_PER_WEBHOOK", unit: "a minute", kind: "lower" },
  { key: "rate_limit_per_project", label: "Runs per project", env: "AGENTCANVAS_RATE_LIMIT_PER_PROJECT", unit: "a minute", kind: "lower" },
  { key: "rate_limit_per_share_client", label: "Messages per share-link visitor", env: "AGENTCANVAS_RATE_LIMIT_PER_SHARE_CLIENT", unit: "a minute", kind: "lower" },
  { key: "max_concurrent_runs", label: "Runs at once", env: "AGENTCANVAS_MAX_CONCURRENT_RUNS", unit: "runs at once (more wait in a queue)", kind: "lower" },
  { key: "max_token_days", label: "Longest API token lifetime", env: "AGENTCANVAS_MAX_TOKEN_DAYS", unit: "days", kind: "lower" },
  { key: "audit_retention_days", label: "Audit log kept", env: "AGENTCANVAS_AUDIT_RETENTION_DAYS", unit: "days (0: forever)", kind: "longer" },
  { key: "memory_retention_days", label: "Long-term memories kept", env: "AGENTCANVAS_MEMORY_RETENTION_DAYS", unit: "days (0: forever)", kind: "any" },
];

const shown = (key: string, v: number | boolean | undefined) =>
  typeof v === "boolean" ? (v ? "allowed" : "not allowed")
    : key.endsWith("_days") ? (v === 0 ? (key === "max_token_days" ? "no limit" : "forever") : `${v} days`)
    : v === 0 ? "no limit" : key === "max_concurrent_runs" ? `${v} at once` : `${v} a minute`;

export function OrgLimits() {
  const qc = useQueryClient();
  const admin = useCan("admin");
  const q = useQuery({ queryKey: ["org-settings"], queryFn: () => apiFetch<OrgSettingsOut>("/org/settings") });
  const [draft, setDraft] = useState<Own>({});
  useEffect(() => { if (q.data) setDraft({ ...(q.data.limits.own as Own) }); }, [q.data]);
  const save = useMutation({
    mutationFn: (limits: Own) => apiFetch<OrgSettingsOut>("/org/settings", { method: "PUT", json: { limits } }),
    onSuccess: (out) => { qc.setQueryData(["org-settings"], out); toast("Saved the organisation's limits"); },
  });
  if (!q.data) return null;
  const { server, effective, own } = q.data.limits as unknown as { server: Own; effective: Own; own: Own };
  const changed = Object.fromEntries(ROWS.filter(({ key }) => (draft[key] ?? null) !== (own[key] ?? null)).map(({ key }) => [key, draft[key] ?? null]));
  const set = (key: string, v: number | boolean | null) => setDraft({ ...draft, [key]: v });
  return (
    <section className="settings-card" aria-label="Organisation limits">
      <h2>Limits and policies</h2>
      <p className="muted small">
        The server's value (set by its operator) applies unless this organisation sets its own. An organisation can
        switch local MCP servers or approving your own runs off but not on, lower a rate limit or the longest API
        token lifetime but not raise it, and keep its audit log longer but not shorter than the server does.
      </p>
      <table className="table org-limits">
        <thead><tr><th>Setting</th><th>Server</th><th>This organisation</th><th>Applies now</th></tr></thead>
        <tbody>
          {ROWS.map(({ key, label, env, unit, kind }) => {
            const ceiling = server[key] as number;
            const forbidden = kind === "switch" && server[key] === false;
            return (
              <tr key={key}>
                <td>{label}<div className="muted small"><code>{env}</code></div></td>
                <td>{shown(key, server[key] as number | boolean)}</td>
                <td>
                  {kind === "switch" ? (
                    <select aria-label={label} value={draft[key] === false ? "off" : ""} disabled={!admin || forbidden}
                      onChange={(e) => set(key, e.target.value === "off" ? false : null)}>
                      <option value="">{forbidden ? "Not allowed by the server" : "Like the server"}</option>
                      {!forbidden && <option value="off">Not allowed</option>}
                    </select>
                  ) : (
                    <span className="row gap">
                      <input type="number" aria-label={label} placeholder="like the server" disabled={!admin}
                        min={kind === "longer" ? Math.min(ceiling, 1) : kind === "any" ? 0 : 1}
                        max={kind === "lower" && ceiling > 0 ? ceiling : unit?.startsWith("days") ? 3650 : 1_000_000}
                        value={draft[key] == null ? "" : String(draft[key])} style={{ width: 130 }}
                        onChange={(e) => set(key, e.target.value.trim() === "" ? null : Number(e.target.value))} />
                      <span className="muted small">{unit}</span>
                    </span>
                  )}
                </td>
                <td data-testid={`applies-${key}`}>{shown(key, effective[key] as number | boolean)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {admin
        ? <button type="button" className="primary" disabled={save.isPending || Object.keys(changed).length === 0}
            onClick={() => save.mutate(changed)}>Save limits</button>
        : <p className="muted small">Only an admin can change these.</p>}
      {save.error && <p className="error-text small" role="alert">Could not save: {(save.error as Error).message}</p>}
    </section>
  );
}
