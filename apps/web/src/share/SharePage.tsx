/** A deployment's public chat page (S23): what a share link opens, full page or compact inside the chat widget's
 * iframe (?embed=1). No sign-in; the visitor's conversations are theirs only (see share.ts). */
import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import {
  answerQuestion, isEmbed, loadShare, rate, sendMessage, ShareError, tokenFromPath,
  type ShareEvent, type ShareInfo, type ShareQuestion,
} from "./share";

interface Message {
  id: number;
  role: "user" | "assistant";
  text: string;
  runId?: string;
  pending?: boolean;
  error?: boolean;
  question?: ShareQuestion;
  rating?: "up" | "down";
}

let nextId = 1;

export function SharePage() {
  const token = tokenFromPath(location.pathname) ?? "";
  const embed = isEmbed(location.search);
  const [info, setInfo] = useState<ShareInfo | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const logEnd = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    document.documentElement.classList.add("share-root");
    loadShare(token)
      .then((i) => { setInfo(i); document.title = i.title; })
      .catch((e: unknown) => setLoadError(e instanceof Error ? e.message : String(e)));
  }, [token]);

  useEffect(() => { logEnd.current?.scrollIntoView?.({ block: "end" }); }, [messages]);

  const waiting = [...messages].reverse().find((m) => m.role === "assistant")?.question;

  const update = (id: number, patch: (m: Message) => Partial<Message>) =>
    setMessages((ms) => ms.map((m) => (m.id === id ? { ...m, ...patch(m) } : m)));

  const run = useCallback(async (start: (onEvent: (e: ShareEvent) => void, signal: AbortSignal) => Promise<void>, userText: string | null) => {
    const reply: Message = { id: nextId++, role: "assistant", text: "", pending: true };
    setMessages((ms) => [...ms, ...(userText !== null ? [{ id: nextId++, role: "user" as const, text: userText }] : []), reply]);
    setBusy(true);
    setNotice(null);
    const ctrl = new AbortController();
    abort.current = ctrl;
    try {
      await start((ev) => {
        if (ev.type === "RUN_STARTED") { setThreadId(ev.threadId); update(reply.id, () => ({ runId: ev.runId })); }
        else if (ev.type === "TEXT_MESSAGE_CONTENT") update(reply.id, (m) => ({ text: m.text + ev.delta }));
        else if (ev.type === "RUN_FINISHED") {
          const q = ev.interrupts[0];
          update(reply.id, (m) => ({
            pending: false, runId: ev.runId,
            text: q ? q.question : ev.answer || m.text || "(no answer)",
            question: q,
          }));
        } else if (ev.type === "RUN_ERROR") update(reply.id, () => ({ pending: false, error: true, text: ev.message }));
      }, ctrl.signal);
      update(reply.id, (m) => (m.pending ? { pending: false, text: m.text || "(no answer)" } : {}));
    } catch (e) {
      if (ctrl.signal.aborted) return;
      const message = e instanceof ShareError ? e.message : "Could not reach the service. Check your connection and try again.";
      update(reply.id, () => ({ pending: false, error: true, text: message }));
    } finally {
      if (abort.current === ctrl) { abort.current = null; setBusy(false); }
      input.current?.focus();
    }
  }, []);

  const send = (text: string) => {
    const t = text.trim();
    if (!t || busy) return;
    setDraft("");
    if (waiting && threadId) {
      void run((on, signal) => answerQuestion(token, threadId, waiting, { text: t }, on, signal), t);
    } else {
      void run((on, signal) => sendMessage(token, t, threadId, on, signal), t);
    }
  };

  const decide = (approve: boolean) => {
    if (!waiting || !threadId || busy) return;
    void run((on, signal) => answerQuestion(token, threadId, waiting, { approve }, on, signal), approve ? "Yes" : "No");
  };

  const restart = () => {
    abort.current?.abort();
    abort.current = null;
    setBusy(false);
    setMessages([]);
    setThreadId(null);
    setDraft("");
    setNotice("Started a new conversation.");
    input.current?.focus();
  };

  const thumbs = async (m: Message, rating: "up" | "down") => {
    if (!m.runId) return;
    try {
      await rate(token, m.runId, rating);
      update(m.id, () => ({ rating }));
      setNotice("Thanks for the feedback.");
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "Could not send the feedback.");
    }
  };

  const onSubmit = (e: FormEvent) => { e.preventDefault(); send(draft); };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(draft); }
  };

  if (loadError) {
    return (
      <main className={`share-page${embed ? " embed" : ""}`}>
        <div className="share-gone" role="alert"><h1>Chat unavailable</h1><p>{loadError}</p></div>
      </main>
    );
  }
  if (!info) return <main className={`share-page${embed ? " embed" : ""}`}><p className="share-loading" role="status">Loading…</p></main>;

  const approval = waiting && (waiting.kind === "approval" || waiting.kind === "tool_approval");
  return (
    <main className={`share-page${embed ? " embed" : ""}`} data-testid="share-page">
      <header className="share-header">
        <h1>{info.title}</h1>
        <button type="button" className="share-new" onClick={restart} disabled={messages.length === 0}>New conversation</button>
      </header>
      <div className="share-log" role="log" aria-live="polite" aria-label="Conversation">
        {info.greeting && <p className="share-msg assistant share-greeting">{info.greeting}</p>}
        {messages.length === 0 && info.suggestions.length > 0 && (
          <ul className="share-suggestions" aria-label="Suggestions">
            {info.suggestions.map((s) => (
              <li key={s}><button type="button" onClick={() => send(s)} disabled={busy}>{s}</button></li>
            ))}
          </ul>
        )}
        {messages.map((m) => (
          <div key={m.id} className={`share-msg ${m.role}${m.error ? " error" : ""}${m.question ? " question" : ""}`}
               data-testid={`share-${m.role}`} aria-busy={m.pending || undefined}>
            <span className="visually-hidden">{m.role === "user" ? "You:" : "Assistant:"} </span>
            {m.text || (m.pending ? <span className="share-typing" aria-label="Writing an answer">…</span> : null)}
            {m.role === "assistant" && !m.pending && !m.error && !m.question && m.runId && (
              <span className="share-rate">
                <button type="button" aria-label="Good answer" aria-pressed={m.rating === "up"} onClick={() => void thumbs(m, "up")}>👍</button>
                <button type="button" aria-label="Bad answer" aria-pressed={m.rating === "down"} onClick={() => void thumbs(m, "down")}>👎</button>
              </span>
            )}
          </div>
        ))}
        <div ref={logEnd} />
      </div>
      {notice && <p className="share-notice" role="status">{notice}</p>}
      {approval && !busy && (
        <div className="share-decide" role="group" aria-label="Your answer">
          <button type="button" className="primary" onClick={() => decide(true)}>Yes</button>
          <button type="button" onClick={() => decide(false)}>No</button>
        </div>
      )}
      <form className="share-composer" onSubmit={onSubmit}>
        <label htmlFor="share-input" className="visually-hidden">{waiting ? "Your answer" : "Your message"}</label>
        <textarea id="share-input" ref={input} rows={1} value={draft} maxLength={20000}
                  placeholder={waiting ? (approval ? "Or reply with a comment…" : "Type your answer…") : "Type a message…"}
                  onChange={(e) => setDraft(e.target.value)} onKeyDown={onKey} />
        <button type="submit" className="primary" disabled={busy || !draft.trim()}>{busy ? "…" : "Send"}</button>
      </form>
      {!embed && <p className="share-foot">Answers are written by an AI assistant and can be wrong.</p>}
    </main>
  );
}
