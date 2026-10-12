/** Playground (S31): chat with a model and its instructions before building anything; then make it a workflow. */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link, useNavigate } from "react-router";
import { apiFetch } from "../api/client";
import type { ChatOut } from "../api/generated";
import { PROJECT_ID, useModels, useSetup } from "../api/queries";
import { ThemeSwitcher } from "../theme/ThemeSwitcher";

interface Turn { role: "user" | "assistant"; content: string }

export function PlaygroundPage() {
  const setup = useSetup();
  const models = useModels();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const defaultModel = setup.data?.default_model?.model ?? "";
  const [model, setModel] = useState("");  // "" = the project's default model
  const [system, setSystem] = useState("You are a helpful assistant.");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [name, setName] = useState("My assistant");
  const chosen = model || undefined;
  const conversation = useRef(0); // bumped by Clear: a reply to an earlier conversation is dropped

  const send = useMutation({
    mutationFn: ({ messages }: { messages: Turn[]; conversation: number }) =>
      apiFetch<ChatOut>(`/projects/${PROJECT_ID}/playground/chat`, {
        method: "POST", json: { messages, system_prompt: system, model: chosen },
      }),
    onSuccess: (out, sent) => {
      if (sent.conversation === conversation.current) setTurns((t) => [...t, { role: "assistant", content: out.reply }]);
    },
  });
  const make = useMutation({
    mutationFn: () => apiFetch<{ id: string }>(`/projects/${PROJECT_ID}/playground/workflow`, {
      method: "POST", json: { name: name.trim() || "My assistant", system_prompt: system, model: chosen },
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
      <header className="topbar">
        <Link to="/" className="brand">AgentCanvas</Link><span className="crumb">/ Playground</span><span className="spacer" />
        <ThemeSwitcher />
      </header>
      <h2>Playground</h2>
      <p className="muted">
        Try a model and its instructions in a chat. When the answers look right, make it a workflow: you get one AI agent
        with these instructions and this model, ready to add tools, approvals and more steps.
      </p>
      <div className="playground-grid">
        <section className="stack">
          <label htmlFor="pg-model">Model</label>
          <select id="pg-model" value={model} onChange={(e) => setModel(e.target.value)}>
            <option value="">{defaultModel ? `Project default (${defaultModel})` : "Project default (not set yet)"}</option>
            {(models.data ?? []).map((m) => <option key={m.id} value={m.id}>{m.label} ({m.id})</option>)}
          </select>
          {!defaultModel && !model && setup.data && (
            <p className="small muted">No default model yet: <Link to="/setup">choose one</Link> or pick a model above.</p>
          )}
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
            {turns.length === 0 && <p className="muted small">Write a message to start.</p>}
            {turns.map((t, i) => (
              <div key={i} className={`bubble ${t.role === "user" ? "human" : "ai"}`}>{t.content}</div>
            ))}
            {send.isPending && <div className="bubble ai streaming" aria-label="Writing an answer">…</div>}
          </div>
          {send.error && <p className="error-text" role="alert">{(send.error as Error).message}</p>}
          <form className="row gap" onSubmit={(e) => { e.preventDefault(); submit(); }}>
            <label htmlFor="pg-input" className="visually-hidden">Your message</label>
            <input id="pg-input" value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Ask something…" />
            <button type="submit" className="primary" disabled={send.isPending || !draft.trim()}>Send</button>
            <button type="button" onClick={() => { conversation.current += 1; setTurns([]); send.reset(); }}
              disabled={turns.length === 0 || send.isPending}>Clear</button>
          </form>
        </section>
      </div>
    </div>
  );
}
