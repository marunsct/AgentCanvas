/** Share a deployment with people (S23): a public chat page behind a link, and a chat widget for any website. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { apiFetch } from "../api/client";
import type { DeploymentOut, ShareOut } from "../api/generated";
import { embedSnippet } from "../share/share";
import { confirmDialog } from "../ui/dialog";

async function copy(text: string, done: (msg: string) => void) {
  try {
    await navigator.clipboard.writeText(text);
    done("Copied.");
  } catch {
    done("Could not copy: select the text and copy it yourself.");
  }
}

export function ShareSection({ deployment }: { deployment: DeploymentOut }) {
  const qc = useQueryClient();
  const key = ["share", deployment.id];
  const share = useQuery({ queryKey: key, queryFn: () => apiFetch<ShareOut>(`/deployments/${deployment.id}/share`) });
  const [title, setTitle] = useState("");
  const [greeting, setGreeting] = useState("");
  const [suggestions, setSuggestions] = useState("");
  const [sites, setSites] = useState("");
  const [aiNotice, setAiNotice] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const seeded = useRef<string | null>(null); // the deployment the form was filled from
  const seed = (d: ShareOut) => {
    setTitle(d.title);
    setGreeting(d.greeting);
    setSuggestions(d.suggestions.join("\n"));
    setSites(d.frame_origins.join("\n"));
    setAiNotice(d.ai_notice);
  };
  // fill the form once per deployment and after a save of its fields, not on every refetch: the switch and a
  // new link update the query too, and that must not wipe what is being typed
  useEffect(() => {
    if (!share.data || seeded.current === deployment.id) return;
    seeded.current = deployment.id;
    seed(share.data);
  }, [share.data, deployment.id]);
  // the default wording changes with the deployment's system record: follow it unless the notice is being reworded
  const served = useRef<string | undefined>(undefined);
  useEffect(() => {
    const now = share.data?.ai_notice;
    if (now === undefined) return;
    const before = served.current; // read now: the updater below runs after the ref has moved on
    if (before !== undefined && now !== before) setAiNotice((typed) => (typed === before ? now : typed));
    served.current = now;
  }, [share.data?.ai_notice]);
  const save = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      apiFetch<ShareOut>(`/deployments/${deployment.id}/share`, { method: "PUT", json: body }),
    onMutate: (body) => { // the switch moves at once
      if (typeof body.enabled === "boolean") qc.setQueryData<ShareOut>(key, (d) => (d ? { ...d, enabled: body.enabled as boolean } : d));
    },
    onSuccess: (out, body) => {
      qc.setQueryData(key, out);
      if ("title" in body) seed(out);
      setNote("Saved.");
    },
    onError: () => { void qc.invalidateQueries({ queryKey: key }); },
  });
  const regenerate = useMutation({
    mutationFn: () => apiFetch<ShareOut>(`/deployments/${deployment.id}/share/regenerate`, { method: "POST" }),
    onSuccess: (out) => { qc.setQueryData(key, out); setNote("New link made. The old link no longer works."); },
  });
  const id = `share-${deployment.id}`;
  const data = share.data;
  const url = data?.path ? `${location.origin}${data.path}` : ""; // no path until the link is first saved
  const error = (share.error ?? save.error ?? regenerate.error) as Error | null;
  return (
    <details className="connect share-box small" data-testid={`share-${deployment.environment}`}>
      <summary>Share as an app or a chat widget</summary>
      <p>
        Turn sharing on to get a link to a chat page anyone can use, no account needed, and a snippet that puts a
        chat button on your website. Anyone who has the link can use it (and your usage limits), so share it with care.
      </p>
      {error && <p className="error-text" role="alert">{error.message}</p>}
      {data && (
        <>
          <label className="row gap share-toggle">
            <input type="checkbox" checked={data.enabled} disabled={save.isPending} data-testid="share-enabled"
                   onChange={(e) => save.mutate({ enabled: e.target.checked })} />
            Anyone with the link can chat with this deployment
          </label>
          <label htmlFor={`${id}-title`}>Title</label>
          <input id={`${id}-title`} value={title} maxLength={120} onChange={(e) => setTitle(e.target.value)} />
          <label htmlFor={`${id}-greeting`}>Greeting</label>
          <textarea id={`${id}-greeting`} value={greeting} maxLength={2000} placeholder="Hi! Ask me anything about…"
                    onChange={(e) => setGreeting(e.target.value)} />
          <label htmlFor={`${id}-suggestions`}>Suggested questions (one per line, up to 6)</label>
          <textarea id={`${id}-suggestions`} value={suggestions} onChange={(e) => setSuggestions(e.target.value)} />
          <label htmlFor={`${id}-sites`}>Sites that may show the chat widget (one per line)</label>
          <textarea id={`${id}-sites`} value={sites} placeholder="https://www.example.com" data-testid="share-sites"
                    onChange={(e) => setSites(e.target.value)} />
          <label htmlFor={`${id}-ai-notice`}>AI notice</label>
          <input id={`${id}-ai-notice`} value={aiNotice} maxLength={200} data-testid="share-ai-notice"
                 aria-describedby={`${id}-ai-notice-help`} aria-invalid={!aiNotice.trim() || undefined}
                 onChange={(e) => setAiNotice(e.target.value)} />
          <p id={`${id}-ai-notice-help`} className={aiNotice.trim() ? "muted" : "error-text"}>
            {aiNotice.trim()
              ? "Always shown on the chat page and in the widget, so visitors know they are chatting with an AI. Reword it, for example in your visitors' language."
              : "The AI notice cannot be empty: visitors must be told they are chatting with an AI."}
          </p>
          <p className="muted">
            {data.frame_origins.length === 0
              ? "No sites listed: any website can put your chat page in a frame. List yours to stop others from doing it."
              : "Only these sites (and this app) can show the chat page in a frame. The link itself still works for anyone."}
            {" "}Use https://example.com, or https://*.example.com for all its subdomains.
          </p>
          {data.inputs.length > 1 && (
            <>
              <label htmlFor={`${id}-input`}>Messages go to</label>
              <select id={`${id}-input`} value={data.input_field ?? ""} disabled={save.isPending}
                      onChange={(e) => save.mutate({ input_field: e.target.value })}>
                {/* "" lets the server pick, and keeps picking if a later version renames the inputs */}
                <option value="">Picked from the workflow's inputs</option>
                {data.inputs.map((f) => <option key={f} value={f}>{f}</option>)}
              </select>
            </>
          )}
          <p className="muted" data-testid="share-input">
            {data.input.field
              ? <>Messages go to the input <code className="inline">{data.input.field}</code>{data.input.chat ? " (as a chat message)" : ""}.</>
              : "This workflow has no text input, so the chat page cannot send it messages."}
          </p>
          <div className="row gap">
            <button type="button" className="small" disabled={save.isPending || !aiNotice.trim()} onClick={() => save.mutate({
              title, greeting, suggestions: suggestions.split("\n").map((s) => s.trim()).filter(Boolean).slice(0, 6),
              frame_origins: sites.split("\n").map((s) => s.trim()).filter(Boolean), ai_notice: aiNotice.trim(),
            })}>Save</button>
          </div>
          {data.enabled && data.token && (
            <>
              <p id={`${id}-link`} className="connect-label">Link</p>
              <code className="mono" aria-labelledby={`${id}-link`} data-testid="share-link">{url}</code>
              <div className="row gap">
                <button type="button" className="small" onClick={() => void copy(url, setNote)}>Copy link</button>
                <a className="small" href={url} target="_blank" rel="noopener noreferrer">Open the page</a>
                <button type="button" className="small" disabled={regenerate.isPending} onClick={async () => {
                  if (await confirmDialog({ title: "Make a new link?", body: "The current link and its conversations stop working.", confirm: "Make a new link", danger: true })) regenerate.mutate();
                }}>New link</button>
              </div>
              <p id={`${id}-embed`} className="connect-label">Chat widget: paste before &lt;/body&gt; on your site</p>
              <pre className="json small" aria-labelledby={`${id}-embed`} data-testid="share-embed">{embedSnippet(location.origin, data.token)}</pre>
              <button type="button" className="small" onClick={() => void copy(embedSnippet(location.origin, data.token ?? ""), setNote)}>Copy snippet</button>
            </>
          )}
          {note && <p className="muted" role="status">{note}</p>}
        </>
      )}
    </details>
  );
}
