/** The editor's "More" menu: the other pages of a workflow and, in Full mode, the less frequent canvas tools, so that
 * the top bar fits a laptop window. It opens below the bar (the bar itself clips what overflows it). Evaluate is on
 * the bar itself. */
import { type KeyboardEvent as ReactKeyboardEvent, useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { useEditor } from "./store";
import { useUi } from "./ui";

export function MoreMenu({ wfId, blast, simple }: { wfId: string; blast: boolean; simple: boolean }) {
  const [at, setAt] = useState<{ top: number; right: number } | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const open = Boolean(at);
  useEffect(() => {
    if (!open) return;
    list.current?.querySelector<HTMLElement>("[role^=menuitem]")?.focus(); // the keyboard lands in the menu
    const close = (e: MouseEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !box.current?.contains(e.target as Node)) {
        setAt(null);
        if (e instanceof KeyboardEvent) trigger.current?.focus(); // and goes back where it came from
      }
    };
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", close);
    return () => { window.removeEventListener("mousedown", close); window.removeEventListener("keydown", close); };
  }, [open]);
  const done = () => setAt(null);
  // arrow keys, Home and End move between the items; Tab leaves the menu and closes it
  const onKey = (e: ReactKeyboardEvent) => {
    const items = [...(list.current?.querySelectorAll<HTMLElement>("[role^=menuitem]") ?? [])];
    const i = items.indexOf(document.activeElement as HTMLElement);
    const to = { ArrowDown: i + 1, ArrowUp: i - 1, Home: 0, End: items.length - 1 }[e.key];
    if (e.key === "Tab") done();
    if (to === undefined || !items.length) return;
    e.preventDefault();
    items[(to + items.length) % items.length].focus();
  };
  return (
    <div ref={box} className="more-menu">
      <button ref={trigger} type="button" aria-haspopup="menu" aria-expanded={open} data-testid="more-menu"
        title={simple ? "Workspace, Knowledge and Memory" : "Workspace, Knowledge, Memory and more canvas tools"}
        onClick={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          setAt(at ? null : { top: r.bottom + 4, right: Math.max(8, window.innerWidth - r.right) });
        }}>More ▾</button>
      {at && (
        <div ref={list} className="more-menu-list" role="menu" style={{ top: at.top, right: at.right }} onKeyDown={onKey}>
          <Link role="menuitem" to={`/workflows/${wfId}/workspace`} data-testid="workspace-link" onClick={done}
            title="This workflow's code (scripts, tools, custom blocks) and the project's shared code">Workspace</Link>
          <Link role="menuitem" to="/knowledge" data-testid="knowledge-link" onClick={done}>Knowledge</Link>
          <Link role="menuitem" to="/memory" data-testid="memory-link" onClick={done}>Memory</Link>
          {!simple && <>
          <hr />
          <button type="button" role="menuitemcheckbox" aria-checked={blast} className={blast ? "active" : ""}
            onClick={() => { useUi.getState().toggleBlast(); done(); }}
            title="Show what each step can do to the outside world, and whether a human approves it first">
            {blast ? "✓ " : ""}Blast radius</button>
          <button type="button" role="menuitem" onClick={() => { void useEditor.getState().group?.(); done(); }}
            title="Turn the selected steps into one subgraph step (Ctrl/⌘+G)">⧉ Subgraph</button>
          <button type="button" role="menuitem" onClick={() => { useEditor.getState().frame?.(); done(); }}
            title="Draw a labelled frame around the selected steps (layout only — nothing changes at run time)">▭ Frame</button>
          </>}
        </div>
      )}
    </div>
  );
}
