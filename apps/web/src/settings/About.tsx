/** Settings › About (everyone): the release and commit the server and this web app run, what support asks for. */
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import type { ReleaseOut as Version } from "../api/generated";
import { QueryStatus } from "../ui/QueryStatus";

function shown(v: Version): string {
  return v.commit ? `${v.version} (commit ${v.commit.slice(0, 12)})` : v.version;
}

export function About() {
  const q = useQuery({ queryKey: ["server-version"], queryFn: () => apiFetch<Version>("/version") });
  const web: Version = { version: __APP_VERSION__, commit: __APP_COMMIT__ || null };
  return (
    <section className="settings-card" aria-label="About">
      <h2>About</h2>
      <p className="muted small">The release this AgentCanvas runs. Quote it when you report a problem.</p>
      <QueryStatus q={q} what="the server's release" />
      <table className="table server-table">
        <tbody>
          <tr><td>Server</td><td data-testid="about-server">{q.data ? shown(q.data) : "…"}</td></tr>
          <tr><td>Web app</td><td data-testid="about-web">{shown(web)}</td></tr>
        </tbody>
      </table>
      {q.data && q.data.version !== web.version && (
        <p className="error-box" role="alert">
          The web app and the server are different releases. Reload the page; if this stays, an upgrade is not finished
          yet: tell your operator.
        </p>
      )}
    </section>
  );
}
