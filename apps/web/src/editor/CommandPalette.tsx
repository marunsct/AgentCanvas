/** Command palette (M2-T12): ⌘/Ctrl+K or double-click the canvas. Add widgets, run, tidy, jump to nodes. */
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { useNodeTypes } from "../api/queries";
import { addElement } from "../canvas/Canvas";
import { useRun } from "../run/runStore";
import { useEditor } from "./store";
import { useUi } from "./ui";

interface Item {
  id: string;
  label: string;
  hint: string;
  run: () => void;
}

export function CommandPalette({ onRun }: { onRun: () => void }) {
  const open = useUi((s) => s.paletteOpen);
  const at = useUi((s) => s.paletteAt);
  const close = useUi((s) => s.closePalette);
  const { data: types } = useNodeTypes();
  const wf = useEditor((s) => s.wf);
  const selected = useEditor((s) => s.selected);
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const [cursor, setCursor] = useState(0);
  const input = useRef<HTMLInputElement>(null);

  const items = useMemo<Item[]>(() => {
    const st = useEditor.getState();
    const add = (types ?? []).map((m) => ({
      id: `add:${m.type}`, label: `Add ${m.label}`, hint: m.category, run: () => addElement(m.type, at ?? undefined),
    }));
    if (at) return add; // double-click: only "add here"
    const actions: Item[] = [
      { id: "run", label: "Run workflow", hint: "action", run: onRun },
      { id: "tidy", label: "Tidy up layout", hint: "action", run: () => void st.tidy?.() },
      { id: "undo", label: "Undo", hint: "Ctrl/⌘+Z", run: st.undo },
      { id: "redo", label: "Redo", hint: "Ctrl/⌘+Shift+Z", run: st.redo },
      { id: "inbox", label: "Open inbox", hint: "navigate", run: () => navigate("/inbox") },
      { id: "reset-run", label: "Clear run overlay", hint: "action", run: () => useRun.getState().setThread(null) },
    ];
    if (selected && wf?.nodes.some((n) => n.id === selected)) {
      actions.unshift({ id: "bp", label: "Toggle breakpoint on selection", hint: "debug", run: () => st.toggleBreakpoint(selected) });
    }
    const go = (wf?.nodes ?? []).map((n) => ({
      id: `go:${n.id}`, label: `Go to ${n.label || n.name}`, hint: n.type, run: () => st.focusNode?.(n.id),
    }));
    return [...actions, ...add, ...go];
  }, [types, wf, selected, at, onRun, navigate]);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return needle ? items.filter((i) => `${i.label} ${i.hint}`.toLowerCase().includes(needle)) : items;
  }, [items, q]);

  useEffect(() => {
    if (open) {
      setQ("");
      setCursor(0);
      setTimeout(() => input.current?.focus(), 0);
    }
  }, [open]);

  if (!open) return null;
  const choose = (i: Item | undefined) => {
    if (!i) return;
    close();
    i.run();
  };
  return (
    <div className="palette-overlay" onMouseDown={close}>
      <div className="command-palette" role="dialog" aria-label="Command palette" onMouseDown={(e) => e.stopPropagation()}>
        <input ref={input} value={q} placeholder={at ? "Add a widget here…" : "Type a command or widget…"}
          aria-label="Search commands"
          onChange={(e) => { setQ(e.target.value); setCursor(0); }}
          onKeyDown={(e) => {
            if (e.key === "Escape") close();
            if (e.key === "ArrowDown") { e.preventDefault(); setCursor((c) => Math.min(c + 1, filtered.length - 1)); }
            if (e.key === "ArrowUp") { e.preventDefault(); setCursor((c) => Math.max(c - 1, 0)); }
            if (e.key === "Enter") choose(filtered[cursor]);
          }} />
        <ul>
          {filtered.slice(0, 12).map((i, k) => (
            <li key={i.id} className={k === cursor ? "active" : ""} onMouseEnter={() => setCursor(k)} onClick={() => choose(i)}>
              {i.label} <small className="muted">{i.hint}</small>
            </li>
          ))}
          {filtered.length === 0 && <li className="muted">No matches</li>}
        </ul>
      </div>
    </div>
  );
}
