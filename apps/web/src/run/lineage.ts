/**
 * Channel lineage (FR-X-18): for the state at one checkpoint, which node (or the run input) last wrote each
 * channel, and at which step. Derived from checkpoint history: a checkpoint's `writes` are the updates of the
 * tasks that ran right after it.
 */
import type { Checkpoint } from "./Timeline";

export interface Write {
  writer: string; // canvas node id, or "input"
  step: number | null; // step of the checkpoint the write produced
  checkpointId: string; // checkpoint the writing step started from
}

/** `ticks` oldest first; returns channel -> last write before (and producing) ticks[index]. */
export function lineageAt(ticks: Checkpoint[], index: number): Record<string, Write> {
  const out: Record<string, Write> = {};
  for (let i = 0; i < index && i < ticks.length; i++) {
    const t = ticks[i];
    for (const [writer, update] of Object.entries(t.writes ?? {})) {
      if (!update || typeof update !== "object" || Array.isArray(update)) continue;
      for (const channel of Object.keys(update)) {
        out[channel] = {
          writer: writer === "__start__" ? "input" : writer,
          step: ticks[i + 1]?.step ?? (t.step ?? 0) + 1,
          checkpointId: t.checkpoint_id,
        };
      }
    }
  }
  return out;
}
