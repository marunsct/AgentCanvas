/** File fields (G-5): a state field whose JSON Schema says `format: "file"` holds a reference to an uploaded file
 * (see apps/api/agentcanvas/ir/files.py). The bytes stay on the server; runs carry `{kind: "file", id, …}`. */

export interface FileRef {
  kind: "file";
  id: string;
  name: string;
  media_type: string;
  size: number;
}

type Schema = Record<string, unknown> | null | undefined;

export const IMAGE_TYPES = ["image/png", "image/jpeg", "image/gif", "image/webp"];

export function fileSchema(accept?: string): Record<string, unknown> {
  return accept ? { type: "object", format: "file", contentMediaType: accept } : { type: "object", format: "file" };
}

export function isFileSchema(schema: Schema): boolean {
  if (!schema || typeof schema !== "object") return false;
  if (schema.format === "file") return true;
  return schema.type === "array" && isFileSchema(schema.items as Schema);
}

/** The media types a file field takes (`image/*`), undefined for any file. */
export function accepts(schema: Schema): string | undefined {
  if (!schema) return undefined;
  if (schema.type === "array") return accepts(schema.items as Schema);
  return typeof schema.contentMediaType === "string" && schema.contentMediaType ? schema.contentMediaType : undefined;
}

/** How the state designer names a field's type: `image` and `file` for file fields, else the JSON Schema type. */
export function typeLabel(schema: Schema): string {
  if (isFileSchema(schema) && schema?.type !== "array") return accepts(schema) === "image/*" ? "image" : "file";
  return (schema?.type as string | undefined) ?? "string";
}

/** The schema a type picked in the state designer stands for. */
export function schemaFor(label: string): Record<string, unknown> {
  if (label === "file") return fileSchema();
  if (label === "image") return fileSchema("image/*");
  return { type: label };
}

export function isFileRef(value: unknown): value is FileRef {
  return Boolean(value) && typeof value === "object" && (value as { kind?: unknown }).kind === "file";
}

export function isImage(mediaType: string | undefined): boolean {
  return IMAGE_TYPES.includes((mediaType ?? "").split(";")[0].trim().toLowerCase());
}
