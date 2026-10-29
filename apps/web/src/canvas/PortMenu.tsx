/** Click a port: "what can connect here?" — compatible blocks to add (already connected) or existing ones to link. */
import { useReactFlow } from "@xyflow/react";
import { useEffect, useMemo, useRef, useState } from "react";
import { ulid } from "ulid";
import { useManifests } from "../api/queries";
import { useEditor } from "../editor/store";
import { useUi } from "../editor/ui";
import { addElement } from "./Canvas";
import { openedByKeyboard, restoreOpener } from "./focus";
import { connectExisting, linkEdge, type NewChoice, suggest } from "./suggest";

/** Tab and Shift+Tab stay inside the open menu instead of walking off into the canvas behind it. */
function trapTab(e: React.KeyboardEvent, box: HTMLElement | null) {
  if (e.key !== "Tab" || !box) return;
  const items = [...box.querySelectorAll<HTMLElement>("button, input")].filter((el) => !el.hasAttribute("disabled"));
  if (!items.length) return;
  const first = items[0]!;
  const last = items[items.length - 1]!;
  const at = document.activeElement;
  if (e.shiftKey && (at === first || !box.contains(at))) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && (at === last || !box.contains(at))) { e.preventDefault(); first.focus(); }
}

export function PortMenu() {
  const menu = useUi((s) => s.portMenu);
  const wf = useEditor((s) => s.wf);
  const drill = useEditor((s) => s.drill);
  const simple = useUi((s) => s.simple);
  const manifests = useManifests();
  const rf = useReactFlow();
  const [q, setQ] = useState("");
  const box = useRef<HTMLDivElement>(null);
  const close = () => useUi.getState().openPortMenu(null);

  useEffect(() => setQ(""), [menu]);
  const open = menu !== null;
  useEffect(() => { // keyboard: the menu takes focus when it opens (from a key), and gives it back to the port on close
    if (!open) return;
    const frame = requestAnimationFrame(() => {
      if (openedByKeyboard() && box.current && !box.current.contains(document.activeElement)) {
        box.current.querySelector<HTMLElement>("input, button.choice")?.focus();
      }
    });
    return () => { cancelAnimationFrame(frame); restoreOpener(); };
  }, [open]);
  useEffect(() => {
    if (!menu) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") close(); };
    const onDown = (e: PointerEvent) => { if (box.current && !box.current.contains(e.target as Node)) close(); };
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onDown, true);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("pointerdown", onDown, true); };
  }, [menu]);

  const s = useMemo(() => (menu && wf ? suggest(wf, manifests, menu, simple) : null), [menu, wf, manifests, simple]);
  if (!menu || !wf || !s || drill.length) return null;

  const needle = q.trim().toLowerCase();
  const adds = s.add.filter((c) => !needle || `${c.label} ${c.blurb} ${c.category}`.toLowerCase().includes(needle));
  const groups = new Map<string, NewChoice[]>();
  for (const c of adds) groups.set(c.category, [...(groups.get(c.category) ?? []), c]);

  const place = () => {
    const at = rf.getNode(menu.node)?.position ?? { x: 0, y: 0 };
    if (menu.side === "in") return { x: at.x - 40, y: at.y + 190 };
    return wf.resources.some((r) => r.id === menu.node) ? { x: at.x + 260, y: at.y - 140 } : { x: at.x + 300, y: at.y };
  };
  const add = (c: NewChoice) => {
    const cur = useEditor.getState().wf;
    if (!cur) return;
    addElement(c.type, place(), undefined, (id) => [{ op: "Connect", edge: linkEdge(cur, menu, id, c.port, `e_${ulid()}`) }]);
    close();
  };
  const x = Math.min(menu.x + 12, window.innerWidth - 340);
  const y = Math.min(menu.y - 20, window.innerHeight - 420);

  return (
    <div className="port-menu" ref={box} style={{ left: Math.max(8, x), top: Math.max(52, y) }} role="dialog"
      aria-label="what can connect here" data-testid="port-menu" onKeyDown={(e) => trapTab(e, box.current)}>
      <div className="row gap">
        <h4>{s.title}</h4>
        <span className="spacer" />
        <button type="button" className="link small" onClick={close} aria-label="close">✕</button>
      </div>
      <p>{s.intro}</p>
      {s.existing.length > 0 && (
        <>
          <h5>Already on the canvas</h5>
          {s.existing.map((c) => (
            <button type="button" key={`${c.id}:${c.port}`} className="choice" data-testid={`port-existing-${c.id}`}
              onClick={() => { useEditor.getState().dispatch(connectExisting(wf, menu, c, `e_${ulid()}`)); close(); }}>
              <b>{c.name}</b>
            </button>
          ))}
        </>
      )}
      {s.add.length > 6 && (
        <input autoFocus placeholder="Search…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="search blocks" />
      )}
      {s.add.length > 0 && <h5>Add a new block</h5>}
      {[...groups.entries()].map(([cat, items]) => (
        <div key={cat}>
          {groups.size > 1 && <small className="muted">{cat}</small>}
          {items.map((c) => (
            <button type="button" key={`${c.type}:${c.port}`} className="choice" data-testid={`port-add-${c.type}`} onClick={() => add(c)}>
              <b>{c.label}</b>
              <small>{c.blurb}</small>
            </button>
          ))}
        </div>
      ))}
      {s.add.length > 0 && adds.length === 0 && <p>Nothing matches “{q}”.</p>}
    </div>
  );
}
