/** Copilot help in the code editor (S17): write code from a description, explain it, or fix it. The answer comes
 * back as a diff to accept or reject; accepting puts it in the editor as an unsaved (undoable) change. */
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { apiFetch } from "../api/client";
import type { CodeHelpOut } from "../api/generated";
import { can } from "../api/session";
import type { LintItem } from "./CodeEditor";
import { lineDiff } from "./diff";
import { KIND_LABEL, kindOf } from "./starters";

type Action = "write" | "explain" | "fix";

export function CodeHelp({ workflowId, path, content, diagnostics, selection, onAccept }: {
  workflowId: string | null;
  path: string;
  content: string;
  diagnostics: LintItem[];
  selection: () => string;
  onAccept: (code: string) => void;
}) {
  const [instruction, setInstruction] = useState("");
  const [answer, setAnswer] = useState<(CodeHelpOut & { action: Action; base: string }) | null>(null);
  const ask = useMutation({
    mutationFn: (action: Action) =>
      apiFetch<CodeHelpOut>(`/workflows/${workflowId}/copilot/code`, {
        method: "POST",
        json: {
          action, path, content, instruction: instruction.trim(), selection: selection() || null,
          problems: diagnostics.map((d) => `line ${d.line}: ${d.code} ${d.message}`),
        },
      }).then((out) => ({ ...out, action, base: content })),
    onSuccess: setAnswer,
  });

  if (!workflowId) {
    return (
      <section className="code-help" aria-label="Copilot code help">
        <p className="muted small">To get the Copilot's help with code, open the Workspace from a workflow (its <b>Workspace</b> button).</p>
      </section>
    );
  }
  const builder = can("builder");
  const kind = KIND_LABEL[kindOf(path)];
  return (
    <section className="code-help" aria-label="Copilot code help">
      <h4>✦ Copilot <span className="muted small">for this {kind.toLowerCase()}</span></h4>
      <textarea rows={3} value={instruction} onChange={(e) => setInstruction(e.target.value)} aria-label="what the code should do"
        placeholder="Describe what the code should do, e.g. “return the number of words in text as word_count”. Select lines in the editor to focus on them." />
      <div className="row gap wrap">
        <button type="button" className="primary" disabled={!builder || ask.isPending || !instruction.trim()}
          onClick={() => ask.mutate("write")} title={builder ? "" : "Only builders can have the Copilot write code"}>Write with Copilot</button>
        <button type="button" disabled={ask.isPending} onClick={() => ask.mutate("explain")}>Explain</button>
        <button type="button" disabled={!builder || ask.isPending} onClick={() => ask.mutate("fix")}>
          Fix{diagnostics.length ? ` ${diagnostics.length} problem${diagnostics.length === 1 ? "" : "s"}` : ""}
        </button>
        {ask.isPending && <span className="muted small" role="status">The Copilot is thinking…</span>}
      </div>
      {ask.error && <p className="error-text small" role="alert">{(ask.error as Error).message}</p>}
      {answer && (
        <div className="code-answer" data-testid="code-answer">
          <p>{answer.text}</p>
          {answer.code !== null && (
            <>
              <pre className="code-diff" data-testid="code-diff" aria-label="proposed changes">
                {lineDiff(answer.base, answer.code).map((l, i) => (
                  <div key={i} className={`diff-${l.kind}`}>{l.kind === "add" ? "+ " : l.kind === "del" ? "- " : "  "}{l.text}</div>
                ))}
              </pre>
              {answer.base !== content && <p className="muted small">The file changed since you asked; accepting replaces it with the Copilot's version.</p>}
              <div className="row gap">
                <button type="button" className="primary" onClick={() => { onAccept(answer.code!); setAnswer(null); }}>Accept changes</button>
                <button type="button" onClick={() => setAnswer(null)}>Reject</button>
                <span className="muted small">Accepted changes are not saved until you press Save.</span>
              </div>
            </>
          )}
          {answer.code === null && <button type="button" onClick={() => setAnswer(null)}>Close</button>}
          <p className="muted small">Answered by {answer.model}</p>
        </div>
      )}
    </section>
  );
}
