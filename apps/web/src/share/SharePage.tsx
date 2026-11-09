/** A deployment's public chat page (S23): what a share link opens, full page or compact inside the chat widget's
 * iframe (?embed=1). No sign-in; the visitor's conversations are theirs only (see share.ts). */
import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import {
  answerQuestion, closeConversation, isEmbed, loadConversation, loadShare, rate, sendMessage, ShareError, tokenFromPath,
  uploadShareFile, type SavedConversation, type ShareEvent, type ShareInfo, type ShareQuestion,
} from "./share";
import { originAllowed, parseCommand, postToParent } from "./embedApi";
import type { FileRef } from "../ir/files";
import { FilePicker } from "../ui/FilePicker";
import { useFollowBottom } from "../ui/useFollowBottom";

interface Message {
  id: number;
  role: "user" | "assistant";
  text: string;
  attached?: string[]; // names of the files sent with a visitor's message
  runId?: string;
  pending?: boolean;
  error?: boolean;
  question?: ShareQuestion;
  rating?: "up" | "down";
}

let nextId = 1;

const sameMessage = (a: Message, b: Message) =>
  a.role === b.role && a.text === b.text && a.runId === b.runId && a.error === b.error
  && a.question?.id === b.question?.id && a.question?.kind === b.question?.kind && a.question?.question === b.question?.question
  && (a.attached ?? []).join("\n") === (b.attached ?? []).join("\n");

/** A saved conversation still being answered is checked every AWAY_POLL_MS, at most AWAY_POLLS times (3 minutes). */
export const AWAY_POLL_MS = 2000;
export const AWAY_POLLS = 90;

export function SharePage() {
  const token = tokenFromPath(location.pathname) ?? "";
  const embed = isEmbed(location.search);
  const [info, setInfo] = useState<ShareInfo | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [files, setFiles] = useState<FileRef[]>([]);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [away, setAway] = useState(false); // a saved conversation whose last message is still being answered
  const abort = useRef<AbortController | null>(null);
  const started = useRef(false); // this page sent something: a saved conversation no longer replaces what is shown
  const input = useRef<HTMLTextAreaElement>(null);

  // the visitor's saved conversation (kept on the server under their browser's secret): shown again after a reload.
  // While an answer is still being written it is fetched again every 2 s: a message keeps its id (the log is
  // append-only), and an unchanged conversation keeps the same state, so the live region does not announce it again
  const restore = useCallback((saved: SavedConversation) => {
    if (started.current) return;
    setMessages((prev) => {
      const next = saved.messages.map((m, i): Message => ({
        id: prev[i]?.id ?? nextId++, role: m.role, text: m.text, runId: m.run_id ?? undefined, error: m.error,
        attached: m.attached, question: m.question ?? undefined, rating: prev[i]?.rating,
      }));
      return next.length === prev.length && next.every((m, i) => sameMessage(m, prev[i])) ? prev : next;
    });
    setThreadId(saved.thread_id);
    setAway(saved.busy);
  }, []);

  useEffect(() => {
    document.documentElement.classList.add("share-root");
    loadShare(token)
      .then((i) => {
        setInfo(i);
        document.title = i.title;
        loadConversation(token).then(restore).catch(() => undefined); // no history is no reason to hide the page
      })
      .catch((e: unknown) => setLoadError(e instanceof Error ? e.message : String(e)));
  }, [token, restore]);

  // the poll used to go on for ever (an answer that never ends, a run the server lost) with the input locked
  const [gaveUp, setGaveUp] = useState(false);
  useEffect(() => {
    if (!away || gaveUp) return;
    let polls = 0;
    const timer = setInterval(() => {
      if (++polls > AWAY_POLLS) { setGaveUp(true); return; }
      loadConversation(token).then(restore).catch(() => undefined);
    }, AWAY_POLL_MS);
    return () => clearInterval(timer);
  }, [away, gaveUp, token, restore]);

  // the postMessage API for the site that frames this page (embedApi.ts): prefill from the parent, events to it
  const allowed = info?.frame_origins;
  useEffect(() => {
    if (!embed || !info) return;
    const onMessage = (e: MessageEvent) => {
      if (e.source !== window.parent || !originAllowed(e.origin, allowed)) return;
      const command = parseCommand(e.data);
      if (command?.type === "prefill") { setDraft(command.text); input.current?.focus(); }
    };
    window.addEventListener("message", onMessage);
    postToParent({ type: "ready" }, allowed);
    return () => window.removeEventListener("message", onMessage);
  }, [embed, info, allowed]);

  // follows the end only while the visitor is at it: one who scrolled up to re-read is left there
  const { end: logEnd, onScroll, follow } = useFollowBottom([messages]);

  const allowedRef = useRef<readonly string[] | undefined>(undefined);
  allowedRef.current = info?.frame_origins;
  const waiting = [...messages].reverse().find((m) => m.role === "assistant")?.question;

  const update = (id: number, patch: (m: Message) => Partial<Message>) =>
    setMessages((ms) => ms.map((m) => (m.id === id ? { ...m, ...patch(m) } : m)));

  const run = useCallback(async (start: (onEvent: (e: ShareEvent) => void, signal: AbortSignal) => Promise<void>, userText: string | null, attached: string[] = []) => {
    const reply: Message = { id: nextId++, role: "assistant", text: "", pending: true };
    setMessages((ms) => [...ms, ...(userText !== null ? [{ id: nextId++, role: "user" as const, text: userText, attached }] : []), reply]);
    setBusy(true);
    setNotice(null);
    follow();
    started.current = true;
    const ctrl = new AbortController();
    abort.current = ctrl;
    try {
      await start((ev) => {
        if (ev.type === "RUN_STARTED") { setThreadId(ev.threadId); update(reply.id, () => ({ runId: ev.runId })); }
        else if (ev.type === "TEXT_MESSAGE_CONTENT") update(reply.id, (m) => ({ text: m.text + ev.delta }));
        else if (ev.type === "RUN_FINISHED") {
          const q = ev.interrupts[0];
          if (embed) {
            postToParent({
              type: "reply", text: q ? q.question : ev.answer, runId: ev.runId, threadId: ev.threadId, question: Boolean(q),
            }, allowedRef.current);
          }
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
  }, [embed, follow]);

  const send = (text: string) => {
    const t = text.trim();
    if ((!t && files.length === 0) || busy || away) return;
    if (waiting && threadId) {
      if (!t) return;
      setDraft("");
      void run((on, signal) => answerQuestion(token, threadId, waiting, { text: t }, on, signal), t);
    } else {
      const sent = files;
      setDraft("");
      setFiles([]);
      void run((on, signal) => sendMessage(token, t, threadId, on, signal, sent), t, sent.map((f) => f.name));
    }
  };

  const decide = (approve: boolean) => {
    if (!waiting || !threadId || busy) return;
    void run((on, signal) => answerQuestion(token, threadId, waiting, { approve }, on, signal), approve ? "Yes" : "No");
  };

  const restart = () => {
    abort.current?.abort();
    abort.current = null;
    started.current = true;
    setAway(false);
    void closeConversation(token).catch(() => undefined);
    setBusy(false);
    setMessages([]);
    setThreadId(null);
    setDraft("");
    setFiles([]);
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
        <h1 dir="auto">{info.title}</h1>
        <button type="button" className="share-new" onClick={restart} disabled={messages.length === 0}>New conversation</button>
      </header>
      <div className="share-log" role="log" aria-live="polite" aria-label="Conversation" onScroll={onScroll}>
        {info.greeting && <p className="share-msg assistant share-greeting" dir="auto">{info.greeting}</p>}
        {messages.length === 0 && info.suggestions.length > 0 && (
          <ul className="share-suggestions" aria-label="Suggestions">
            {info.suggestions.map((s) => (
              <li key={s}><button type="button" dir="auto" onClick={() => send(s)} disabled={busy}>{s}</button></li>
            ))}
          </ul>
        )}
        {messages.map((m) => (
          <div key={m.id} className={`share-msg ${m.role}${m.error ? " error" : ""}${m.question ? " question" : ""}`}
               data-testid={`share-${m.role}`} aria-busy={m.pending || undefined}>
            <span className="visually-hidden">{m.role === "user" ? "You:" : "Assistant:"} </span>
            {m.attached?.map((n) => <span key={n} className="share-attached">📎 {n}</span>)}
            {/* dir=auto on the text alone: the bubble starts with the English "Assistant:" label, which would make
                Arabic or Hebrew text left-to-right (punctuation on the wrong side, numbers and links reordered) */}
            {m.text ? <span className="share-text" dir="auto">{m.text}</span>
              : m.pending ? <span className="share-typing" aria-label="Writing an answer">…</span> : null}
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
      {away && !gaveUp && <p className="share-notice" role="status">Your last message is still being answered…</p>}
      {away && gaveUp && (
        <p className="share-notice" role="status" data-testid="share-gave-up">
          Your last message is taking a long time to answer.{" "}
          <button type="button" className="link" onClick={() => setGaveUp(false)}>Check again</button>{" "}
          <button type="button" className="link" onClick={() => { setAway(false); setGaveUp(false); }}>Stop waiting</button>
        </p>
      )}
      {notice && <p className="share-notice" role="status">{notice}</p>}
      {approval && !busy && (
        <div className="share-decide" role="group" aria-label="Your answer">
          <button type="button" className="primary" onClick={() => decide(true)}>Yes</button>
          <button type="button" onClick={() => decide(false)}>No</button>
        </div>
      )}
      {info.attachments && !waiting && (
        <FilePicker label={info.attachments.field.replace(/_/g, " ")} accept={info.attachments.accept ?? undefined}
          multiple={info.attachments.multiple} value={files} onChange={setFiles} disabled={busy}
          upload={(f) => uploadShareFile(token, f)} />
      )}
      <form className="share-composer" onSubmit={onSubmit}>
        <label htmlFor="share-input" className="visually-hidden">{waiting ? "Your answer" : "Your message"}</label>
        <textarea id="share-input" ref={input} rows={1} value={draft} maxLength={20000} dir="auto"
                  placeholder={waiting ? (approval ? "Or reply with a comment…" : "Type your answer…") : "Type a message…"}
                  onChange={(e) => setDraft(e.target.value)} onKeyDown={onKey} />
        <button type="submit" className="primary" disabled={busy || (!draft.trim() && files.length === 0)}>{busy ? "…" : "Send"}</button>
      </form>
      {!embed && <p className="share-foot">Answers are written by an AI assistant and can be wrong.</p>}
    </main>
  );
}
