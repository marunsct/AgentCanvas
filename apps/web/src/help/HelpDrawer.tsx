/** The beginner guide: what an agentic workflow is, the words on the canvas, and a first build — no AI knowledge assumed. */
import { useEffect } from "react";
import { useEditor } from "../editor/store";
import { useUi } from "../editor/ui";
import { startTour } from "../tutor/Tutor";

const SEEN = "agentcanvas.helpSeen";

function seen(): boolean {
  try { return localStorage.getItem(SEEN) === "1"; } catch { return true; } // no storage: don't pop up every time
}

/** Open the guide by itself once, the first time someone opens an empty workflow. */
export function useFirstVisitHelp() {
  const empty = useEditor((s) => Boolean(s.wf && s.wf.nodes.length === 0 && s.wf.resources.length === 0));
  useEffect(() => {
    if (empty && !seen()) useUi.getState().setHelp(true);
  }, [empty]);
}

export function HelpDrawer() {
  const open = useUi((s) => s.help);
  const wf = useEditor((s) => s.wf);
  if (!open) return null;
  const close = () => {
    try { localStorage.setItem(SEEN, "1"); } catch { /* ignore */ }
    useUi.getState().setHelp(false);
  };
  const tour = () => {
    if (!wf) return;
    startTour(wf.id);
    close();
  };
  return (
    <aside className="help-drawer" aria-label="beginner guide" data-testid="help-drawer">
      <div className="row gap">
        <h3>Welcome! Here is how this works</h3>
        <button type="button" className="link help-close" onClick={close} aria-label="close the guide">✕</button>
      </div>

      <h4>What you are building</h4>
      <p>
        A <b>workflow</b> is a recipe that a computer follows, one step at a time. Some steps use <b>AI</b> to read,
        write or decide; others ask a <b>person</b>, call a web service, or tidy up data. An <b>agentic</b> workflow
        is one where an AI can also decide what to do next and use tools on its own.
      </p>
      <p className="muted small">Example: read a customer email → AI decides if it is urgent → urgent ones go to a person
        for approval → AI drafts a reply.</p>

      <h4>The words you will see</h4>
      <dl>
        <dt>Block / step</dt>
        <dd>A box on the canvas that does one thing, like <i>Ask AI</i> or <i>Ask for approval</i>. Drag them in from the left.</dd>
        <dt>▶ Start and ■ Finish</dt>
        <dd>Every run begins at Start and ends at Finish. Steps in between run in the order of the arrows.</dd>
        <dt>Arrows (grey)</dt>
        <dd>Decide what runs next. Drag from the dot on the right of a block to the dot on the top-left of the next one.</dd>
        <dt>AI model</dt>
        <dd>The AI “brain” (Claude, GPT, Gemini, a local model…). AI steps need one plugged into their <i>model</i> input.</dd>
        <dt>Add-ons (dashed outline)</dt>
        <dd>Things that plug into a step instead of running on their own: AI models, tools, limits, safety checks.
          They connect with coloured, dashed lines.</dd>
        <dt>Data fields (channels)</dt>
        <dd>The workflow's notepad: named boxes like <code>question</code> and <code>answer</code> that steps read from
          and write to. Click empty canvas to add them in the panel on the right. In text you write, insert one with
          <code>{"{{ state.question }}"}</code>.</dd>
        <dt>Agent</dt>
        <dd>An AI that works in a loop: it thinks, uses a tool, looks at the result, and repeats until the job is done.</dd>
        <dt>Tool</dt>
        <dd>Something an agent can use, such as calling a web service or running a Python function.</dd>
        <dt>Run</dt>
        <dd>Trying the workflow with some input. Open the <b>Run</b> tab (or press <b>▶ Test</b>).</dd>
      </dl>

      <h4>Build your first workflow (5 steps)</h4>
      <ol>
        <li>Click empty canvas. In the right panel press <b>+ Channel</b> twice and name the fields <code>question</code> and <code>answer</code>.</li>
        <li>Click the dot on the right of <b>▶ Start</b> and choose <b>Ask AI</b>. It is added and connected for you.</li>
        <li>In the right panel, write the instructions, e.g. <code>{"Answer simply: {{ state.question }}"}</code>, and set <i>Save the reply in</i> to <code>answer</code>.</li>
        <li>Click the coloured square marked <i>model</i> on the Ask AI block and choose <b>AI model</b>. Then click the Ask AI block's right-hand dot and choose <b>Finish</b>.</li>
        <li>Open the <b>Run</b> tab, type <code>{'{"question": "What is an AI agent?"}'}</code> and press <b>▶ Run</b>.</li>
      </ol>

      <h4>Getting unstuck</h4>
      <ul>
        <li><b>Hover</b> over any dot on a block to see what it is for.</li>
        <li><b>Click</b> a dot to see everything that can connect to it, and add it in one click.</li>
        <li>A red <b>⛔</b> on a block means something is missing. The <b>Problems</b> list at the bottom says what happened and how to fix it; click a problem to jump to the block.</li>
        <li>If an arrow refuses to connect, a message at the bottom of the canvas explains why.</li>
        <li>Made a mistake? <b>↶</b> (or Ctrl/⌘+Z) undoes it. Everything saves automatically.</li>
        <li>Prefer to start from a working example? The home page has ready-made templates.</li>
      </ul>

      <div className="row gap">
        <button type="button" className="primary" onClick={tour} data-testid="help-tour">Guide me step by step</button>
        <button type="button" onClick={close}>Got it</button>
      </div>
      <p className="muted small">You can reopen this guide any time with <b>❓ Help</b> at the top.</p>
    </aside>
  );
}
