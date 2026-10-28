/** A safety net for failures nobody handled: a rejected promise from a fire-and-forget call (`void save()`) used to
 * vanish into the browser console and leave the person looking at a screen that did not change. */
import { useEffect, useState } from "react";
import { ApiError } from "../api/client";

/** What to tell the person about an unhandled failure, or null when it is not worth a banner (the person left the
 * page or cancelled, a 401 that sends them to sign in anyway, a browser's harmless layout notice). */
export function describeFailure(reason: unknown): string | null {
  if (reason instanceof DOMException && reason.name === "AbortError") return null;
  if (reason instanceof ApiError) return reason.status === 401 ? null : reason.message;
  const text = reason instanceof Error ? reason.message : typeof reason === "string" ? reason : "";
  if (!text || /ResizeObserver loop/i.test(text)) return null;
  return text.length > 300 ? `${text.slice(0, 300)}…` : text;
}

export function GlobalErrors() {
  const [message, setMessage] = useState<string | null>(null);
  useEffect(() => {
    const show = (reason: unknown) => {
      const text = describeFailure(reason);
      if (text) setMessage(text);
    };
    const onRejection = (e: PromiseRejectionEvent) => show(e.reason);
    const onError = (e: ErrorEvent) => show(e.error ?? e.message);
    window.addEventListener("unhandledrejection", onRejection);
    window.addEventListener("error", onError);
    return () => {
      window.removeEventListener("unhandledrejection", onRejection);
      window.removeEventListener("error", onError);
    };
  }, []);
  useEffect(() => {
    if (!message) return;
    const timer = setTimeout(() => setMessage(null), 12_000);
    return () => clearTimeout(timer);
  }, [message]);
  if (!message) return null;
  return (
    <div className="toast error" role="alert" data-testid="global-error">
      Something went wrong: {message}
      <button type="button" className="link" onClick={() => setMessage(null)}>Dismiss</button>
    </div>
  );
}
