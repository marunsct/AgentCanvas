/**
 * Structured-output / answer-form designer (FR-AGENT-10, M3-T3, M3-T7): edit a flat object schema as a list of
 * fields, or the raw JSON Schema for anything richer. Used for config properties with `format: json_schema`.
 */
import type { FieldProps } from "@rjsf/utils";
import { useEffect, useState } from "react";

type Json = Record<string, unknown>;
export type FieldType = "string" | "number" | "integer" | "boolean" | "enum" | "list";
export interface FieldRow {
  name: string;
  type: FieldType;
  description: string;
  required: boolean;
  options: string; // enum values, comma separated
}

const TYPES: FieldType[] = ["string", "number", "integer", "boolean", "enum", "list"];
const TOP_KEYS = new Set(["title", "type", "properties", "required", "description", "additionalProperties"]);
const PROP_KEYS = new Set(["type", "description", "enum", "items"]);

/** Rows for a flat object schema; null when the schema uses features the field list cannot show. */
export function rowsFromSchema(schema: Json | undefined | null): FieldRow[] | null {
  if (!schema || Object.keys(schema).length === 0) return [];
  if (schema.type !== "object" || Object.keys(schema).some((k) => !TOP_KEYS.has(k))) return null;
  const required = new Set((schema.required as string[] | undefined) ?? []);
  const rows: FieldRow[] = [];
  for (const [name, raw] of Object.entries((schema.properties as Record<string, Json>) ?? {})) {
    if (Object.keys(raw).some((k) => !PROP_KEYS.has(k))) return null;
    const base = { name, description: String(raw.description ?? ""), required: required.has(name), options: "" };
    if (Array.isArray(raw.enum)) rows.push({ ...base, type: "enum", options: raw.enum.join(", ") });
    else if (raw.type === "array" && (raw.items as Json | undefined)?.type === "string") rows.push({ ...base, type: "list" });
    else if (["string", "number", "integer", "boolean"].includes(raw.type as string) && !raw.items) {
      rows.push({ ...base, type: raw.type as FieldType });
    } else return null;
  }
  return rows;
}

export function schemaFromRows(rows: FieldRow[], title?: string): Json | undefined {
  const named = rows.filter((r) => r.name.trim());
  if (!named.length) return undefined;
  const properties: Record<string, Json> = {};
  for (const r of named) {
    const prop: Json =
      r.type === "enum" ? { type: "string", enum: r.options.split(",").map((s) => s.trim()).filter(Boolean) }
        : r.type === "list" ? { type: "array", items: { type: "string" } }
          : { type: r.type };
    if (r.description.trim()) prop.description = r.description.trim();
    properties[r.name.trim()] = prop;
  }
  const required = named.filter((r) => r.required).map((r) => r.name.trim());
  return { ...(title ? { title } : {}), type: "object", properties, ...(required.length ? { required } : {}), additionalProperties: false };
}

export function JsonSchemaField(props: FieldProps) {
  const { schema, formData, onChange, fieldPathId, disabled, readonly } = props;
  const value = (formData && typeof formData === "object" ? formData : undefined) as Json | undefined;
  const title = typeof value?.title === "string" ? value.title : undefined;
  // rows are local editing state: a row being renamed may briefly have no name (and so no schema property)
  const [rows, setRowsState] = useState<FieldRow[] | null>(() => rowsFromSchema(value));
  const [mode, setMode] = useState<"fields" | "json">(rows === null ? "json" : "fields");
  const [draft, setDraft] = useState(() => (value ? JSON.stringify(value, null, 2) : ""));
  const [error, setError] = useState<string | null>(null);
  const emit = (v: Json | undefined) => onChange(v, fieldPathId.path);
  const off = disabled || readonly;
  const valueKey = JSON.stringify(value ?? null);
  useEffect(() => {
    // changed elsewhere (undo, JSON mode, another tab): rebuild the rows unless they already produce this value
    setRowsState((cur) =>
      cur !== null && JSON.stringify(schemaFromRows(cur, title) ?? null) === valueKey ? cur : rowsFromSchema(value));
  }, [valueKey]); // valueKey stands for `value` (a new object on every render)
  const setRows = (next: FieldRow[]) => {
    setRowsState(next);
    const v = schemaFromRows(next, title);
    setDraft(v ? JSON.stringify(v, null, 2) : "");
    emit(v);
  };
  const edit = (i: number, patch: Partial<FieldRow>) => rows && setRows(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));

  return (
    <fieldset className="schema-field" id={fieldPathId.$id}>
      <legend>{schema.title}</legend>
      {schema.description && <p className="muted small">{schema.description}</p>}
      <div className="row gap mode-toggle" role="radiogroup" aria-label={`${schema.title} editor`}>
        <button type="button" role="radio" aria-checked={mode === "fields"} className={mode === "fields" ? "active" : ""}
          disabled={rowsFromSchema(value) === null} title={rowsFromSchema(value) === null ? "this schema needs the JSON editor" : undefined}
          onClick={() => { setRowsState(rowsFromSchema(value)); setMode("fields"); }}>Fields</button>
        <button type="button" role="radio" aria-checked={mode === "json"} className={mode === "json" ? "active" : ""}
          onClick={() => { setDraft(value ? JSON.stringify(value, null, 2) : ""); setMode("json"); }}>JSON</button>
      </div>
      {mode === "fields" && rows !== null ? (
        <>
          {rows.map((r, i) => (
            <div key={i} className="schema-row">
              <input className="mono" value={r.name} placeholder="field" aria-label="field name" disabled={off}
                onChange={(e) => edit(i, { name: e.target.value.replace(/[^A-Za-z0-9_]/g, "_") })} />
              <select value={r.type} aria-label="field type" disabled={off} onChange={(e) => edit(i, { type: e.target.value as FieldType })}>
                {TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
              <label className="row" title="required"><input type="checkbox" checked={r.required} disabled={off}
                onChange={(e) => edit(i, { required: e.target.checked })} />req</label>
              <button type="button" className="link" aria-label={`remove ${r.name}`} disabled={off}
                onClick={() => setRows(rows.filter((_, j) => j !== i))}>×</button>
              {r.type === "enum" && (
                <input value={r.options} placeholder="low, high" aria-label="enum options" disabled={off}
                  onChange={(e) => edit(i, { options: e.target.value })} />
              )}
              <input value={r.description} placeholder="description (helps the model)" aria-label="field description"
                disabled={off} onChange={(e) => edit(i, { description: e.target.value })} />
            </div>
          ))}
          <button type="button" disabled={off}
            onClick={() => setRows([...rows, { name: `field_${rows.length + 1}`, type: "string", description: "", required: true, options: "" }])}>
            + Field
          </button>
        </>
      ) : (
        <>
          <textarea className={`mono${error ? " invalid" : ""}`} rows={8} value={draft} disabled={off}
            aria-label={`${schema.title} JSON`} spellCheck={false}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={() => {
              if (!draft.trim()) { setError(null); emit(undefined); return; }
              try {
                const parsed = JSON.parse(draft) as unknown;
                if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("must be a JSON object");
                setError(null);
                emit(parsed as Json);
              } catch (e) {
                setError((e as Error).message);
              }
            }} />
          {error && <div className="field-error">{error}</div>}
        </>
      )}
    </fieldset>
  );
}
