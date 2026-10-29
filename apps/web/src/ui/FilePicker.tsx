/** Attach files to a file field (G-5): drag and drop or a picker, a preview for images, remove. The page that uses it
 * says how a file reaches the server (`upload`): the Run tab sends it to the project, the share page to the link. */
import { useEffect, useRef, useState, type DragEvent } from "react";
import { isImage, type FileRef } from "../ir/files";

export const MAX_FILE_MB = 10; // the server's default; it has the last word (AGENTCANVAS_MAX_UPLOAD_MB)

/** Why a file cannot be attached, before it is sent: too large, or not the kind the field takes (`image/*`). */
export function refuse(file: Pick<File, "name" | "size" | "type">, accept?: string, maxMb = MAX_FILE_MB): string | null {
  if (file.size > maxMb * 1024 * 1024) return `“${file.name}” is larger than ${maxMb} MB.`;
  if (file.size === 0) return `“${file.name}” is empty.`;
  if (accept && file.type) {
    const ok = accept.split(",").map((a) => a.trim().toLowerCase()).some((a) =>
      a === "*/*" || a === file.type.toLowerCase() || (a.endsWith("/*") && file.type.toLowerCase().startsWith(a.slice(0, -1))));
    if (!ok) return `“${file.name}” is not ${accept === "image/*" ? "an image" : `a ${accept} file`}.`;
  }
  return null;
}

export interface FilePickerProps {
  label: string;
  accept?: string;
  multiple?: boolean;
  value: FileRef[];
  upload: (file: File) => Promise<FileRef>;
  onChange: (files: FileRef[]) => void;
  disabled?: boolean;
  maxMb?: number;
}

export function FilePicker({ label, accept, multiple, value, upload, onChange, disabled, maxMb }: FilePickerProps) {
  const [busy, setBusy] = useState(0);
  const [problem, setProblem] = useState<string | null>(null);
  const [over, setOver] = useState(false);
  const previews = useRef(new Map<string, string>()); // file id -> object URL of the picked file, images only
  const input = useRef<HTMLInputElement>(null);
  const current = useRef(value);
  current.current = value;
  useEffect(() => () => previews.current.forEach((u) => URL.revokeObjectURL(u)), []);

  const add = async (picked: File[]) => {
    setProblem(null);
    const files = multiple ? picked : picked.slice(0, 1);
    let next = multiple ? [...current.current] : [];
    for (const file of files) {
      const why = refuse(file, accept, maxMb);
      if (why) { setProblem(why); continue; }
      setBusy((n) => n + 1);
      try {
        const ref = await upload(file);
        // the preview shows only what the server says is an image, never what the name or the browser claims
        if (isImage(ref.media_type)) previews.current.set(ref.id, URL.createObjectURL(file));
        next = multiple ? [...next, ref] : [ref];
        onChange(next);
      } catch (e) {
        setProblem(e instanceof Error ? e.message : "The file could not be sent.");
      } finally {
        setBusy((n) => n - 1);
      }
    }
  };
  const drop = (e: DragEvent) => {
    e.preventDefault();
    setOver(false);
    if (!disabled) void add(Array.from(e.dataTransfer.files));
  };
  const remove = (id: string) => onChange(value.filter((f) => f.id !== id));

  return (
    <div className={`file-picker${over ? " over" : ""}`} data-testid={`file-${label}`}
      onDragOver={(e) => { e.preventDefault(); if (!disabled) setOver(true); }} onDragLeave={() => setOver(false)} onDrop={drop}>
      <div className="row gap">
        <button type="button" disabled={disabled || busy > 0} onClick={() => input.current?.click()}>
          {busy > 0 ? "Sending…" : multiple ? `Attach files to ${label}` : `Attach a file to ${label}`}
        </button>
        <span className="muted small">or drop {multiple ? "files" : "a file"} here</span>
        <input ref={input} type="file" hidden multiple={multiple} accept={accept} aria-label={`${label} file`}
          onChange={(e) => { void add(Array.from(e.target.files ?? [])); e.target.value = ""; }} />
      </div>
      {value.length > 0 && (
        <ul className="file-list">
          {value.map((f) => (
            <li key={f.id}>
              {previews.current.get(f.id) && isImage(f.media_type)
                ? <img src={previews.current.get(f.id)} alt={`Preview of ${f.name}`} className="file-preview" />
                : <span className="file-icon" aria-hidden="true">{f.media_type === "application/pdf" ? "PDF" : "FILE"}</span>}
              <span className="file-name">{f.name} <span className="muted small">({formatSize(f.size)})</span></span>
              <button type="button" className="icon-btn" aria-label={`remove ${f.name}`} disabled={disabled} onClick={() => remove(f.id)}>✕</button>
            </li>
          ))}
        </ul>
      )}
      {problem && <div className="field-error" role="alert">{problem}</div>}
    </div>
  );
}

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
