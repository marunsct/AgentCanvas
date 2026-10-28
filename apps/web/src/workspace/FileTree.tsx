/** The workspace's files as folders that open and close, instead of one long list of paths. A file's button is
 * named by its path (as the list was), and its folder starts open when it holds the open file. */
import type { FileOut } from "../api/generated";

interface Folder { name: string; path: string; folders: Map<string, Folder>; files: { name: string; path: string; shown: string }[] }

export function buildTree(files: FileOut[], strip = ""): Folder {
  const root: Folder = { name: "", path: "", folders: new Map(), files: [] };
  for (const f of files) {
    const shown = strip && f.path.startsWith(strip) ? f.path.slice(strip.length) : f.path;
    const parts = shown.split("/");
    let at = root;
    parts.slice(0, -1).forEach((part, i) => {
      const path = parts.slice(0, i + 1).join("/");
      if (!at.folders.has(part)) at.folders.set(part, { name: part, path, folders: new Map(), files: [] });
      at = at.folders.get(part)!;
    });
    at.files.push({ name: parts[parts.length - 1], path: f.path, shown });
  }
  return root;
}

function Branch({ folder, selected, onOpen }: { folder: Folder; selected: string | null; onOpen: (path: string) => void }) {
  const folders = [...folder.folders.values()].sort((a, b) => a.name.localeCompare(b.name));
  const files = [...folder.files].sort((a, b) => a.name.localeCompare(b.name));
  return (
    <ul className="file-tree">
      {folders.map((f) => (
        <li key={f.path}>
          <details open={Boolean(selected?.includes(`${f.path}/`)) || f.files.length + f.folders.size <= 6}>
            <summary className="folder">{f.name}/</summary>
            <Branch folder={f} selected={selected} onOpen={onOpen} />
          </details>
        </li>
      ))}
      {files.map((f) => (
        <li key={f.path}>
          <button type="button" className={`${f.path === selected ? "active" : ""}${f.name === "__init__.py" ? " quiet" : ""}`}
            aria-label={f.shown} title={f.path} onClick={() => onOpen(f.path)}>{f.name}</button>
        </li>
      ))}
    </ul>
  );
}

export function FileTree({ files, strip, selected, onOpen, label }: {
  files: FileOut[]; strip?: string; selected: string | null; onOpen: (path: string) => void; label: string;
}) {
  return <nav aria-label={label}><Branch folder={buildTree(files, strip)} selected={selected} onOpen={onOpen} /></nav>;
}
