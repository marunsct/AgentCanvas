/** JSON-Schema helpers for inspector forms. */

type Schema = Record<string, unknown>;

/** Map `format` keywords (prompt, channel, cel, template, model, import_path, regex) to RJSF widgets, and
 * `json_schema` (an object-valued schema, e.g. structured output) to the schema designer field. */
export function uiSchemaFromFormats(schema: Schema): Schema {
  const ui: Schema = {};
  const fmt = schema.format;
  if (typeof fmt === "string" && WIDGET_FORMATS.has(fmt)) ui["ui:widget"] = fmt;
  if (fmt === "json_schema") return { "ui:field": "json_schema" };
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

export const WIDGET_FORMATS = new Set(["prompt", "template", "channel", "cel", "model", "import_path", "regex"]);
export const CUSTOM_FORMATS = [...WIDGET_FORMATS, "json_schema"];

/** Top-level JSON merge patch turning `before` into `after` (removed keys -> null). */
export function diffToMergePatch(before: Record<string, unknown>, after: Record<string, unknown>): Record<string, unknown> {
  const patch: Record<string, unknown> = {};
  for (const k of Object.keys(before)) if (!(k in after) || after[k] === undefined) patch[k] = null;
  for (const [k, v] of Object.entries(after)) {
    if (v !== undefined && JSON.stringify(v) !== JSON.stringify(before[k])) patch[k] = v;
  }
  return patch;
}

/** Tokens ≈ characters / 4 (display hint only). */
export const estimateTokens = (text: string) => Math.ceil(text.length / 4);
