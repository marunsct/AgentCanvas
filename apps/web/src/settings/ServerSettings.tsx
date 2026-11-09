/** Settings › Server (admins): how this server is set up, read-only, with the environment variable that changes
 * each setting. Operators set these; the page is here so an admin need not ask what they are. */
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import type { ServerSetting } from "../api/generated";
import { QueryStatus } from "../ui/QueryStatus";

export function ServerSettings() {
  const q = useQuery({ queryKey: ["server-settings"], queryFn: () => apiFetch<ServerSetting[]>("/server/settings") });
  const groups = [...new Set((q.data ?? []).map((r) => r.group))];
  return (
    <section className="settings-card" aria-label="Server">
      <h2>Server</h2>
      <p className="muted small">How this server is set up. These are set by its operator with the variables shown; they cannot be changed here.</p>
      <QueryStatus q={q} what="the server settings" />
      {groups.map((g) => (
        <div key={g}>
          <h3>{g}</h3>
          <table className="table server-table">
            <tbody>
              {q.data!.filter((r) => r.group === g).map((r) => (
                <tr key={r.label}><td>{r.label}</td><td>{r.value}</td><td><code>{r.env}</code></td></tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </section>
  );
}
