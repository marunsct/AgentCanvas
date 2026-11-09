/** File fields in the Run tab (G-5): which input channels take files, sending one to the project, and putting the
 * attached files into the run's input as references. */
import { apiFetch } from "../api/client";
import { PROJECT_ID } from "../api/queries";
import { accepts, isFileSchema, type FileRef } from "../ir/files";
import type { WF } from "../ir/model";

export interface FileField { name: string; accept?: string; multiple: boolean }

/** The input channels that take a file (or a list of files), in channel order. */
export function fileFields(wf: WF): FileField[] {
  return wf.state.channels
    .filter((c) => ((c.io ?? "inout") === "inout" || c.io === "in") && isFileSchema(c.type))
    .map((c) => ({ name: c.name, accept: accepts(c.type), multiple: (c.type as { type?: string }).type === "array" }));
}

/** Send a file to the project; `accept` (image/*) makes the server refuse other kinds at once. */
export function uploadProjectFile(file: File, accept?: string): Promise<FileRef> {
  const form = new FormData();
  form.append("file", file);
  return apiFetch<FileRef>(`/projects/${PROJECT_ID}/uploads${accept ? `?accept=${encodeURIComponent(accept)}` : ""}`, { method: "POST", body: form });
}

/** The run input with each field's attached files: one reference for a file field, a list for a list of files. */
export function withFiles(input: Record<string, unknown>, fields: FileField[], attached: Record<string, FileRef[]>) {
  const out = { ...input };
  for (const f of fields) {
    const files = attached[f.name] ?? [];
    if (f.multiple) out[f.name] = files;
    else if (files[0]) out[f.name] = files[0];
    else delete out[f.name];
  }
  return out;
}
