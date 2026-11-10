/** The app's own confirm and prompt dialogs, and its success toasts, in place of `window.confirm`/`window.prompt`
 * (which block the page, cannot be styled, and read "localhost says"). Imperative, so a click handler stays one line:
 * `if (await confirmDialog({ title: "Delete X?", confirm: "Delete", danger: true })) remove.mutate()`. */
import { useEffect, useRef, useState } from "react";
import { create } from "zustand";

interface Ask {
  title: string;
  body?: React.ReactNode;
  confirm?: string; // the confirming button's label (default "OK")
  cancel?: string;
  danger?: boolean; // the confirming button reads as destructive
  input?: { value?: string; label?: string; placeholder?: string; required?: boolean };
}
interface Open extends Ask { id: number; resolve: (v: string | boolean | null) => void }
interface Toast { id: number; text: string; kind: "ok" | "info" }

const useDialogs = create<{ open: Open[]; toasts: Toast[] }>(() => ({ open: [], toasts: [] }));
let seq = 0;

function ask(a: Ask): Promise<string | boolean | null> {
  return new Promise((resolve) => {
    const id = ++seq;
    useDialogs.setState((s) => ({
      open: [...s.open, { ...a, id, resolve: (v) => { useDialogs.setState((t) => ({ open: t.open.filter((o) => o.id !== id) })); resolve(v); } }],
    }));
  });
}

/** True when the person confirms, false when they cancel (button, Escape, or a click outside). */
export async function confirmDialog(a: Omit<Ask, "input">): Promise<boolean> {
  return (await ask(a)) === true;
}

/** The text entered, or null when cancelled. An `input.required` dialog cannot be confirmed empty. */
export async function promptDialog(a: Ask & { input: NonNullable<Ask["input"]> }): Promise<string | null> {
  const v = await ask(a);
  return typeof v === "string" ? v : null;
}

/** A short success or information notice that goes away by itself (errors stay on the page, where they happened). */
export function toast(text: string, kind: Toast["kind"] = "ok"): void {
  const id = ++seq;
  useDialogs.setState((s) => ({ toasts: [...s.toasts.slice(-2), { id, text, kind }] }));
  setTimeout(() => useDialogs.setState((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })), 4000);
}

/** Mounted once, at the root: renders whatever is open. */
export function DialogHost() {
  const open = useDialogs((s) => s.open);
  const toasts = useDialogs((s) => s.toasts);
  return (
    <>
      {open.length > 0 && <DialogView key={open[0].id} d={open[0]} />}
      {/* the live region is always there (a screen reader only announces into one that exists); each notice is a status */}
      <div className="toasts" aria-live="polite" data-testid="toasts">
        {toasts.map((t) => <div key={t.id} role="status" className={`toast-item ${t.kind}`}>{t.text}</div>)}
      </div>
    </>
  );
}

function DialogView({ d }: { d: Open }) {
  const [value, setValue] = useState(d.input?.value ?? "");
  const box = useRef<HTMLFormElement>(null);
  const before = useRef<Element | null>(null);
  const cancel = () => d.resolve(d.input ? null : false);
  // a destructive confirm starts on Cancel (WAI-ARIA dialog pattern): a stray or repeated Enter must not delete
  const start = d.danger && !d.input ? "button.cancel" : "input, button.confirm";
  useEffect(() => {
    before.current = document.activeElement;
    const first = box.current?.querySelector<HTMLElement>(start);
    first?.focus();
    if (first instanceof HTMLInputElement) first.select();
    return () => { if (before.current instanceof HTMLElement) before.current.focus(); };
  }, [start]); // the view is keyed by the dialog: this runs once
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") { e.stopPropagation(); cancel(); }
    if (e.key !== "Tab" || !box.current) return; // keep focus inside the dialog
    const all = [...box.current.querySelectorAll<HTMLElement>("input, button")].filter((el) => !el.hasAttribute("disabled"));
    const [first, last] = [all[0], all[all.length - 1]];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  };
  const blocked = Boolean(d.input?.required && !value.trim());
  return (
    <div className="dialog-overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) cancel(); }}>
      <form ref={box} className="dialog" role="dialog" aria-modal="true" aria-labelledby={`dlg-${d.id}`} onKeyDown={onKey}
        data-testid="dialog" onSubmit={(e) => { e.preventDefault(); if (!blocked) d.resolve(d.input ? value : true); }}>
        <h2 id={`dlg-${d.id}`}>{d.title}</h2>
        {d.body && <div className="dialog-body">{d.body}</div>}
        {d.input && (
          <label className="dialog-field">
            {d.input.label && <span>{d.input.label}</span>}
            <input value={value} placeholder={d.input.placeholder} aria-label={d.input.label ?? d.title}
              onChange={(e) => setValue(e.target.value)} />
          </label>
        )}
        <div className="dialog-actions">
          <button type="button" className="cancel" onClick={cancel}>{d.cancel ?? "Cancel"}</button>
          <button type="submit" className={`confirm ${d.danger ? "danger-solid" : "primary"}`} disabled={blocked}>{d.confirm ?? "OK"}</button>
        </div>
      </form>
    </div>
  );
}
