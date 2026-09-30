/** JSON-Schema helpers for inspector forms. */

type Schema = Record<string, unknown>;

/** Map `format` keywords (prompt, channel, cel, template, model, import_path, regex) to RJSF widgets, and
 * `json_schema` (an object-valued schema, e.g. structured output) to the schema designer field. */
export function uiSchemaFromFormats(schema: Schema): Schema {
  const ui: Schema = {};
  const fmt = schema.format;
  if (typeof fmt === "string" && WIDGET_FORMATS.has(fmt)) ui["ui:widget"] = fmt;
  if (fmt === "json_schema") return { "ui:field": "json_schema" };
  if (fmt === "subgraph") return { "ui:field": "subgraph" };
  const props = schema.properties as Record<string, Schema> | undefined;
  if (props) for (const [k, sub] of Object.entries(props)) {
    const child = uiSchemaFromFormats(sub);
    if (Object.keys(child).length) ui[k] = child;
  }
  const items = schema.items as Schema | undefined;
  if (items && typeof items === "object") {
    const child = uiSchemaFromFormats(items);
    if (Object.keys(child).length) ui.items = child;
  }
  const extra = schema.additionalProperties as Schema | undefined;
  if (extra && typeof extra === "object") {
    const child = uiSchemaFromFormats(extra);
    if (Object.keys(child).length) ui.additionalProperties = child;
  }
  return ui;
}

export const WIDGET_FORMATS = new Set(["prompt", "template", "channel", "cel", "model", "import_path", "regex", "url_template"]);
export const CUSTOM_FORMATS = [...WIDGET_FORMATS, "json_schema", "subgraph"];

const isObject = (v: unknown): v is Record<string, unknown> => Boolean(v) && typeof v === "object" && !Array.isArray(v);

/**
 * RFC 7396 merge patch turning `before` into `after`: removed keys -> null, nested objects are diffed recursively
 * (a merge patch *merges* objects, so replacing a nested object wholesale would keep its old keys — e.g. a renamed
 * structured-output field or a renamed `values` key). Arrays and scalars are replaced.
 */
export function diffToMergePatch(before: Record<string, unknown>, after: Record<string, unknown>): Record<string, unknown> {
  const patch: Record<string, unknown> = {};
  for (const k of Object.keys(before)) if (!(k in after) || after[k] === undefined) patch[k] = null;
  for (const [k, v] of Object.entries(after)) {
    if (v === undefined) continue;
    const old = before[k];
    if (isObject(v) && isObject(old)) {
      const sub = diffToMergePatch(old, v);
      if (Object.keys(sub).length) patch[k] = sub;
    } else if (JSON.stringify(v) !== JSON.stringify(old)) patch[k] = v;
  }
  return patch;
}

/** Tokens ≈ characters / 4 (display hint only). */
export const estimateTokens = (text: string) => Math.ceil(text.length / 4);
