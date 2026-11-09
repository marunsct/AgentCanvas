/** A small pill for a status, a role or a stability mark. The tone picks the colour; the words carry the meaning
 * (colour is never the only signal). */
export type Tone = "neutral" | "ok" | "warn" | "err" | "running" | "beta";

const CLASS: Record<Tone, string> = { neutral: "", ok: "ok", warn: "warn", err: "err", running: "status-running", beta: "beta" };

export function Badge({ tone = "neutral", title, children }: { tone?: Tone; title?: string; children: React.ReactNode }) {
  return <span className={`badge${CLASS[tone] ? ` ${CLASS[tone]}` : ""}`} title={title}>{children}</span>;
}
