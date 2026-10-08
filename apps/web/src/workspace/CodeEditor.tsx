/** CodeMirror 6 editor for workspace files (M5-T2): Python/JSON highlighting, ruff diagnostics as markers. */
import { json } from "@codemirror/lang-json";
import { python } from "@codemirror/lang-python";
import { type Diagnostic, lintGutter, setDiagnostics } from "@codemirror/lint";
import { Compartment, EditorState, type Text } from "@codemirror/state";
import { oneDark } from "@codemirror/theme-one-dark";
import { keymap } from "@codemirror/view";
import { basicSetup, EditorView } from "codemirror";
import { useEffect, useRef } from "react";
import { resolved, useTheme } from "../theme/theme";

/** One Dark in the medium and dark themes, the default (light) look otherwise. */
const look = (dark: boolean) => (dark ? oneDark : []);

export interface LintItem {
  line: number;
  column: number;
  end_line: number;
  end_column: number;
  code: string;
  message: string;
}

/** Server positions are 1-based line/column; CodeMirror wants document offsets (clamped to the text). */
export function toDiagnostics(doc: Text, items: LintItem[]): Diagnostic[] {
  const pos = (line: number, col: number) => {
    const l = doc.line(Math.min(Math.max(line, 1), doc.lines));
    return Math.min(l.from + Math.max(col - 1, 0), l.to);
  };
  return items.map((d) => {
    const from = pos(d.line, d.column);
    const to = Math.max(pos(d.end_line, d.end_column), from);
    return { from, to, severity: d.code.startsWith("W") ? "warning" : "error", message: `${d.code} ${d.message}`, source: "ruff" };
  });
}

export function CodeEditor({ path, value, diagnostics, onChange, onSave }: {
  path: string; value: string; diagnostics: LintItem[]; onChange: (text: string) => void; onSave: () => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const handlers = useRef({ onChange, onSave });
  const theme = useRef(new Compartment());
  const dark = resolved(useTheme((s) => s.theme)) !== "light";
  handlers.current = { onChange, onSave };

  useEffect(() => {
    if (!host.current) return;
    const lang = path.endsWith(".py") ? [python()] : path.endsWith(".json") ? [json()] : [];
    view.current = new EditorView({
      parent: host.current,
      state: EditorState.create({
        doc: value,
        extensions: [
          basicSetup, ...lang, lintGutter(), theme.current.of(look(dark)),
          keymap.of([{ key: "Mod-s", preventDefault: true, run: () => { handlers.current.onSave(); return true; } }]),
          EditorView.updateListener.of((u) => { if (u.docChanged) handlers.current.onChange(u.state.doc.toString()); }),
        ],
      }),
    });
    return () => view.current?.destroy();
    // a new editor per file; `value` is only the initial text
  }, [path]);

  useEffect(() => {
    view.current?.dispatch({ effects: theme.current.reconfigure(look(dark)) });
  }, [dark]);

  useEffect(() => {
    const v = view.current;
    if (v) v.dispatch(setDiagnostics(v.state, toDiagnostics(v.state.doc, diagnostics)));
  }, [diagnostics]);

  return <div ref={host} className="code-editor" data-testid="code-editor" />;
}
