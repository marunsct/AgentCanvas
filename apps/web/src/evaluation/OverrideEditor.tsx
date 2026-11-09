/** An experiment variant's changes as rows: which block, which of its settings, the new value (a model, a prompt, a
 * temperature...), instead of typing `{"llm": {"model": "..."}}`. The value field shows the current one. */
import { useManifests } from "../api/queries";
import { readValue } from "./ExampleForm";

export type Overrides = Record<string, Record<string, unknown>>;
interface Element { id: string; name: string; label?: string | null; type: string; config?: Record<string, unknown> }
interface Row { element: string; field: string; value: string }

const show = (v: unknown) => (v === undefined ? "" : typeof v === "string" ? v : JSON.stringify(v));

export function toRows(o: Overrides): Row[] {
  return Object.entries(o).flatMap(([element, fields]) => Object.entries(fields).map(([field, v]) => ({ element, field, value: show(v) })));
}

export function fromRows(rows: Row[]): Overrides {
  const out: Overrides = {};
  for (const r of rows) {
    if (!r.element || !r.field) continue;
    (out[r.element] ??= {})[r.field] = readValue(r.value);
  }
  return out;
}

export function OverrideEditor({ elements, rows, onChange, label }: {
  elements: Element[]; rows: Row[]; onChange: (rows: Row[]) => void; label: string;
}) {
  const manifests = useManifests();
  const fieldsOf = (name: string) => {
    const el = elements.find((e) => e.name === name);
    if (!el) return [];
    const schema = manifests.get(el.type)?.config_schema as { properties?: Record<string, unknown> } | undefined;
    return [...new Set([...Object.keys(schema?.properties ?? {}), ...Object.keys(el.config ?? {})])];
  };
  const set = (i: number, patch: Partial<Row>) => onChange(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  return (
    <div className="override-editor" role="group" aria-label={label}>
      {rows.map((r, i) => {
        const el = elements.find((e) => e.name === r.element);
        return (
          <div key={i} className="row gap small">
            <span className="muted">change</span>
            <select aria-label={`${label} change ${i + 1} block`} value={r.element} onChange={(e) => set(i, { element: e.target.value, field: "", value: "" })}>
              <option value="">block…</option>
              {elements.map((e) => <option key={e.id} value={e.name}>{e.label || e.name}</option>)}
            </select>
            <select aria-label={`${label} change ${i + 1} setting`} value={r.field} disabled={!r.element}
              onChange={(e) => set(i, { field: e.target.value, value: show(el?.config?.[e.target.value]) })}>
              <option value="">setting…</option>
              {fieldsOf(r.element).map((f) => <option key={f} value={f}>{f}</option>)}
            </select>
            <span className="muted">to</span>
            <input aria-label={`${label} change ${i + 1} value`} value={r.value} disabled={!r.field} placeholder={show(el?.config?.[r.field])}
              onChange={(e) => set(i, { value: e.target.value })} />
            <button type="button" className="link small" aria-label={`remove change ${i + 1}`} onClick={() => onChange(rows.filter((_, j) => j !== i))}>×</button>
          </div>
        );
      })}
      <button type="button" className="link small" onClick={() => onChange([...rows, { element: "", field: "", value: "" }])}>
        + Change a setting
      </button>
    </div>
  );
}
