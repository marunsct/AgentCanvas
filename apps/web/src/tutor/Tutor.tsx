/** Guided build (FR-X-24): a card that teaches one concept per step and advances when the canvas shows it done. */
import { useEffect, useState } from "react";
import { Link } from "react-router";
import { useEditor } from "../editor/store";
import { useRun } from "../run/runStore";
import { currentStep, STEPS } from "./steps";

const KEY = "agentcanvas.tour"; // workflow id the tour runs on (per browser tab)

export function startTour(workflowId: string) {
  try { sessionStorage.setItem(KEY, workflowId); } catch { /* storage unavailable: the tour just won't persist */ }
  window.dispatchEvent(new Event(TOUR_EVENT));
}

const TOUR_EVENT = "agentcanvas:tour"; // a tour started while the editor is open (from the beginner guide)

function tourFor(): string | null {
  try { return sessionStorage.getItem(KEY); } catch { return null; }
}

export function Tutor() {
  const wf = useEditor((s) => s.wf);
  const [active, setActive] = useState(() => tourFor());
  const finishedSeq = useRun((s) => s.runSeq);
  const [startSeq] = useState(() => useRun.getState().runSeq); // runSeq is cumulative: count only runs after mount
  const interrupts = useRun((s) => s.interrupts.length);
  const error = useRun((s) => s.error);
  const [seen, setSeen] = useState({ finished: false, interrupted: false });
  useEffect(() => {
    const on = () => setActive(tourFor());
    window.addEventListener(TOUR_EVENT, on);
    return () => window.removeEventListener(TOUR_EVENT, on);
  }, []);
  useEffect(() => {
    if (finishedSeq > startSeq && !error) setSeen((s) => ({ ...s, finished: true }));
  }, [finishedSeq, startSeq, error]);
  useEffect(() => {
    if (interrupts > 0) setSeen((s) => ({ ...s, interrupted: true }));
  }, [interrupts]);
  if (!wf || active !== wf.id) return null;
  const i = currentStep(wf, seen);
  const close = () => { try { sessionStorage.removeItem(KEY); } catch { /* ignore */ } setActive(null); };

  if (i >= STEPS.length) {
    return (
      <aside className="tutor" aria-label="guided build" data-testid="tutor">
        <h4>🎉 You built a human-in-the-loop agent workflow</h4>
        <p className="small">State, steps, flow, resources, runs and interrupts — the whole model. Next: try a template, save a run as a test case, or publish it from the Versions tab.</p>
        <div className="row gap"><Link to="/" className="small">Templates</Link><span className="spacer" /><button type="button" className="small" onClick={close}>Close</button></div>
      </aside>
    );
  }
  const step = STEPS[i];
  return (
    <aside className="tutor" aria-label="guided build" data-testid="tutor">
      <div className="row gap">
        <small className="muted">Guided build · step {i + 1} of {STEPS.length}</small>
        <span className="spacer" />
        <button type="button" className="link small" onClick={close} aria-label="close the guided build">✕</button>
      </div>
      <div className="tutor-progress" aria-hidden="true">
        {STEPS.map((s, k) => <span key={s.id} className={k < i ? "done" : k === i ? "now" : ""} />)}
      </div>
      <h4 data-testid="tutor-step">{step.title}</h4>
      <p className="small">{step.concept}</p>
      <p className="small"><b>Do:</b> {step.todo}</p>
    </aside>
  );
}
