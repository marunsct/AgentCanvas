/** Playground (S31): chat with a model and its instructions before building anything; then make it a workflow. */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router";
import { apiFetch } from "../api/client";
import { postStream } from "../run/sse";
import { Markdown } from "../ui/Markdown";
import { PROJECT_ID, useModels, useSetup } from "../api/queries";
import { PageHeader } from "../ui/PageHeader";
import { takesTemperature } from "./playground";

interface Turn { role: "user" | "assistant"; content: string }

// the conversation is kept while the app is open in this tab (moving to another page and back keeps it)
const KEY = "agentcanvas.playground";
function remembered(): Turn[] {
  try { return JSON.parse(sessionStorage.getItem(KEY) ?? "[]") as Turn[]; } catch { return []; }
}

export function PlaygroundPage() {
  const setup = useSetup();
  const models = useModels();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const defaultModel = setup.data?.default_model?.model ?? "";
  const [model, setModel] = useState("");  // "" = the project's default model
  const [system, setSystem] = useState("You are a helpful assistant.");
  const [turns, setTurns] = useState<Turn[]>(remembered);
  const [live, setLive] = useState("");
  const stream = useRef<AbortController | null>(null);
  useEffect(() => { try { sessionStorage.setItem(KEY, JSON.stringify(turns)); } catch { /* not kept */ } }, [turns]);
  const [draft, setDraft] = useState("");
  const [name, setName] = useState("My assistant");
  const [temperature, setTemperature] = useState<number | null>(null); // null = the model's (or project's) own
  const chosen = model || undefined;
  const sampling = takesTemperature(models.data, model || defaultModel);
  const temp = sampling && temperature !== null ? temperature : undefined; // sent only when the model takes one
  const conversation = useRef(0); // bumped by Clear: a reply to an earlier conversation is dropped

  const send = useMutation({
    // the answer streams in as it is written; Stop keeps what came so far
    mutationFn: async ({ messages, conversation: mine }: { messages: Turn[]; conversation: number }) => {
      const ctl = (stream.current = new AbortController());
      let reply = "";
      let failure: string | null = null;
      try {
        await postStream(`/projects/${PROJECT_ID}/playground/chat/stream`, { messages, system_prompt: system, model: chosen, temperature: temp }, (raw) => {
          const e = raw as unknown as { type: string; text?: string; reply?: string; message?: string };
          if (e.type === "delta" && e.text) { reply += e.text; if (mine === conversation.current) setLive(reply); }
          if (e.type === "error") failure = e.message ?? "the model call failed";
        }, ctl.signal);
      } catch (err) {
        if (!ctl.signal.aborted) throw err;
      } finally {
        setLive("");
        stream.current = null;
      }
      if (reply && mine === conversation.current) setTurns((t) => [...t, { role: "assistant", content: reply + (ctl.signal.aborted ? " …(stopped)" : "") }]);
      if (failure) throw new Error(failure);
    },
  });
  const make = useMutation({
    mutationFn: () => apiFetch<{ id: string }>(`/projects/${PROJECT_ID}/playground/workflow`, {
      method: "POST", json: { name: name.trim() || "My assistant", system_prompt: system, model: chosen, temperature: temp },
    }),
    onSuccess: (wf) => {
      void qc.invalidateQueries({ queryKey: ["workflows"] });
      navigate(`/workflows/${wf.id}`);
    },
  });
  const submit = () => {
    const text = draft.trim();
    if (!text || send.isPending) return;
    const next: Turn[] = [...turns, { role: "user", content: text }];
    setTurns(next);
    setDraft("");
    send.mutate({ messages: next, conversation: conversation.current });
  };

  return (
    <div className="page playground" data-testid="playground">
      <PageHeader title="Playground" description={<>Try a model and its instructions in a chat. When the answers look right, make it a workflow: you get one AI agent
        with these instructions and this model, ready to add tools, approvals and more steps.</>} />
      <div className="playground-grid">
        <section className="stack">
          <label htmlFor="pg-model">Model</label>
          <select id="pg-model" value={model} onChange={(e) => setModel(e.target.value)}>
            <option value="">{defaultModel ? `Project default (${defaultModel})` : "Project default (not set yet)"}</option>
            {(models.data ?? []).map((m) => <option key={m.id} value={m.id}>{m.label} ({m.id})</option>)}
          </select>
          {!defaultModel && !model && setup.data && (
            <p className="small muted">No default model yet: <Link to="/settings/models">choose one</Link> or pick a model above.</p>
          )}
          <label htmlFor="pg-temperature">Temperature</label>
          <div className="row gap">
            <input id="pg-temperature" type="range" min={0} max={2} step={0.1} disabled={!sampling}
              value={temperature ?? 1} aria-valuetext={temperature === null ? "the model's default" : String(temperature)}
              onChange={(e) => setTemperature(Number(e.target.value))} />
            <span className="small" data-testid="pg-temperature-value">{!sampling || temperature === null ? "default" : temperature.toFixed(1)}</span>
            {sampling && temperature !== null && <button type="button" className="small" onClick={() => setTemperature(null)}>Reset</button>}
          </div>
          <p className="small muted">
            {sampling
              ? "Lower answers more predictably, higher more varied. Default: the model's own, or the project's setting."
              : "This model takes no temperature: it always uses its own sampling."}
          </p>
          <label htmlFor="pg-system">Instructions</label>
          <textarea id="pg-system" rows={8} value={system} onChange={(e) => setSystem(e.target.value)}
            placeholder="Who the assistant is and how it answers" />
          <label htmlFor="pg-name">Workflow name</label>
          <input id="pg-name" value={name} onChange={(e) => setName(e.target.value)} />
          <button type="button" className="primary" data-testid="pg-make" disabled={make.isPending} onClick={() => make.mutate()}>
            Make it a workflow</button>
          {make.error && <p className="error-text" role="alert">{(make.error as Error).message}</p>}
        </section>
        <section className="chat">
          <div className="chat-log" role="log" aria-live="polite" aria-label="Conversation" data-testid="pg-log">
            {turns.length === 0 && <p className="muted small empty-hint">Write a message to try the model with these instructions.</p>}
            {turns.map((t, i) => (
              <div key={i} className={`bubble ${t.role === "user" ? "human" : "ai"}`}>{t.role === "user" ? t.content : <Markdown text={t.content} />}</div>
            ))}
            {send.isPending && (live
              ? <div className="bubble ai"><div className="streaming"><Markdown text={live} /></div></div>
              : <div className="bubble ai" aria-label="Writing an answer">…</div>)}
          </div>
          {send.error && <p className="error-text" role="alert">{(send.error as Error).message}</p>}
          <form className="row gap" onSubmit={(e) => { e.preventDefault(); submit(); }}>
            <label htmlFor="pg-input" className="visually-hidden">Your message</label>
            <input id="pg-input" value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Ask something…" />
            {send.isPending
              ? <button type="button" onClick={() => stream.current?.abort()}>■ Stop</button>
              : <button type="submit" className="primary" disabled={!draft.trim()}>Send</button>}
            <button type="button" onClick={() => { conversation.current += 1; setTurns([]); send.reset(); }}
              disabled={turns.length === 0 || send.isPending}>Clear</button>
          </form>
        </section>
      </div>
    </div>
  );
}
