/** Events as code (FR-X-22): the Event Map (events → handlers → workflows), test/replay with dry run, event log. */
import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { apiFetch } from "../api/client";
import { PROJECT_ID, useWorkspaceCatalog } from "../api/queries";
import { QueryStatus } from "../ui/QueryStatus";
import type { EventLogOut as LoggedEvent } from "../api/generated";
import { PageHeader } from "../ui/PageHeader";
import { useCan } from "../api/session";
import { ago, exact } from "../ui/time";

interface HandlerResult {
  handler: string;
  matched: boolean;
  actions: Record<string, unknown>[];
  skipped: string | null;
  error: string | null;
  runs: string[];
}

interface EventResult {
  event_id: string | null;
  type: string;
  status: string;
  handlers: HandlerResult[];
}


// read per request: the project is chosen after sign-in, and may change
const base = () => `/projects/${PROJECT_ID}/events`;
const PAGE = 50;

export function EventsPage() {
  const qc = useQueryClient();
  const canBuild = useCan("builder");
  const catalog = useWorkspaceCatalog();
  const [only, setOnly] = useState("");
  const log = useInfiniteQuery({
    queryKey: ["events", PROJECT_ID, only],
    initialPageParam: "",
    queryFn: ({ pageParam }) => {
      const q = new URLSearchParams({ limit: String(PAGE) });
      if (only) q.set("type", only);
      if (pageParam) q.set("before", pageParam);
      return apiFetch<LoggedEvent[]>(`${base()}?${q}`);
    },
    getNextPageParam: (last) => (Array.isArray(last) && last.length === PAGE ? last[last.length - 1].id : undefined),
    refetchInterval: 5000,
  });
  // a page of another shape (a server of another version) shows as nothing, not a crash
  const logged = log.data?.pages.flatMap((p) => (Array.isArray(p) ? p : [])) ?? [];
  const [type, setType] = useState("");
  const [data, setData] = useState("{}");
  const [result, setResult] = useState<EventResult | null>(null);
  let parsed: Record<string, unknown> | null = null;
  try {
    parsed = JSON.parse(data || "{}") as Record<string, unknown>;
  } catch {
    parsed = null;
  }
  const send = useMutation({
    mutationFn: (dry: boolean) => apiFetch<EventResult>(dry ? `${base()}/dry-run` : base(), { method: "POST", json: { type, data: parsed } }),
    onSuccess: (r) => { setResult(r); void qc.invalidateQueries({ queryKey: ["events", PROJECT_ID] }); },
  });
  const handlers = catalog.data?.handlers ?? [];
  const types = [...new Set([...handlers.map((h) => h.type), ...(catalog.data?.schemas ?? [])])].sort();
  // the filter also offers what has been logged (unfiltered pages are kept while a filter is on)
  const unfiltered = qc.getQueryData<{ pages: unknown[] }>(["events", PROJECT_ID, ""])?.pages ?? [];
  const loggedTypes = unfiltered.flatMap((p) => (Array.isArray(p) ? (p as LoggedEvent[]).map((e) => e.type) : []));
  const filterTypes = [...new Set([...types, ...loggedTypes, ...(only ? [only] : [])])].sort();

  return (
    <div className="page events-page">
      <PageHeader title="Events" description={<>Generated from <code>events/*.py</code> in the <Link to="/workspace">workspace</Link>:
        which events start or resume which workflows.</>} />
      {handlers.length === 0 && <p className="muted" data-testid="no-handlers">No handlers yet — add <code>@on("type")</code> functions in <code>events/</code>.</p>}
      <table className="table event-map" data-testid="event-map">
        {handlers.length > 0 && <thead><tr><th>Event</th><th aria-hidden /><th>Handler</th><th aria-hidden /><th>Starts</th></tr></thead>}
        <tbody>
          {handlers.map((h) => (
            <tr key={h.import_path}>
              <td><code>{h.type}</code></td>
              <td>→</td>
              <td><b>{h.name}</b>{h.filter && <div className="muted small">if {h.filter}</div>}</td>
              <td>→</td>
              <td>{h.targets.length ? h.targets.join(", ") : <span className="muted">resume / emit</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {(catalog.data?.problems ?? []).map((p, i) => (
        <p key={i} className="warn-text small">⚠ {(p as { code?: string }).code ?? ""} {p.path}: {p.message}</p>
      ))}

      {!canBuild && <p className="muted small">Your role can see the event map and log; a builder or admin can send test events.</p>}
      {canBuild && <>
      <h3>Send a test event</h3>
      <div className="event-form">
        <input list="event-types" placeholder="crm.lead.created" value={type} onChange={(e) => setType(e.target.value)} aria-label="event type" />
        <datalist id="event-types">{types.map((t) => <option key={t} value={t} />)}</datalist>
        <textarea className={`mono${parsed ? "" : " invalid"}`} rows={4} value={data} onChange={(e) => setData(e.target.value)} aria-label="event data" />
        <div className="row gap">
          <button type="button" disabled={!type || !parsed || send.isPending} onClick={() => send.mutate(true)}>Dry run</button>
          <button type="button" className="primary" disabled={!type || !parsed || send.isPending} onClick={() => send.mutate(false)}>Send</button>
        </div>
        {send.error && <p className="error-text">{(send.error as Error).message}</p>}
      </div>
      {result && <ResultView result={result} />}
      </>}

      <div className="row gap wrap">
        <h3>Event log</h3>
        <select aria-label="filter events by type" value={only} onChange={(e) => setOnly(e.target.value)}>
          <option value="">Every type</option>
          {filterTypes.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
      </div>
      <table className="table" data-testid="event-log">
        <thead><tr><th>Type</th><th>Source</th><th>Status</th><th>When</th><th /></tr></thead>
        <tbody>
          {(log.isLoading || log.isError) && <tr><td colSpan={5}><QueryStatus q={log} what="the event log" /></td></tr>}
          {log.isSuccess && logged.length === 0 && (
            <tr><td colSpan={5}><div className="empty-state">{only ? `No ${only} events.` : "No events yet. Send a test event above."}</div></td></tr>
          )}
          {logged.map((e) => (
            <tr key={e.id}>
              <td><code>{e.type}</code></td>
              <td>{e.source}</td>
              <td className={`status-${e.status}`}>{e.status}</td>
              <td className="muted small" title={exact(e.created_at)}>{ago(e.created_at)}</td>
              <td>{canBuild && (
                <button type="button" className="link" onClick={() => { setType(e.type); setData(JSON.stringify(e.data, null, 2)); setResult(null); }}>
                  replay
                </button>
              )}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {log.hasNextPage && (
        <button type="button" disabled={log.isFetchingNextPage} onClick={() => void log.fetchNextPage()}>
          {log.isFetchingNextPage ? "Loading…" : "Load older"}
        </button>
      )}
    </div>
  );
}

function ResultView({ result }: { result: EventResult }) {
  return (
    <div className="event-result" data-testid="event-result">
      <p><b>{result.status}</b>{result.event_id && <small className="muted"> · {result.event_id}</small>}</p>
      {result.handlers.length === 0 && <p className="muted">No handler subscribes to <code>{result.type}</code>.</p>}
      <ul>
        {result.handlers.map((h) => (
          <li key={h.handler}>
            <code>{h.handler}</code>{" "}
            {h.skipped ? <span className="muted">skipped ({h.skipped})</span> : h.error ? <span className="error-text">{h.error}</span> : null}
            {h.actions.map((a, i) => <div key={i} className="mono small">→ {JSON.stringify(a)}</div>)}
            {h.runs.length > 0 && <div className="small">started {h.runs.join(", ")}</div>}
          </li>
        ))}
      </ul>
    </div>
  );
}
