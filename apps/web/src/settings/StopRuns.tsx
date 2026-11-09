/** Settings › Organisation › General: the kill switch (F-5). An admin stops every run in flight in the organisation;
 * the reason goes to the audit log. Runs on other servers stop at their next heartbeat (about 10 seconds). */
import { useMutation } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import type { StopRunsOut } from "../api/generated";
import { useCan } from "../api/session";
import { promptDialog, toast } from "../ui/dialog";

export function StopRuns() {
  const admin = useCan("admin");
  const stop = useMutation({
    mutationFn: (reason: string) => apiFetch<StopRunsOut>("/org/runs/stop", { method: "POST", json: { reason } }),
    onSuccess: (out) => {
      const n = out.stopping.length + out.cancelled.length;
      toast(n === 0 ? "No runs were in flight" : `Stopping ${n} run${n === 1 ? "" : "s"}`);
    },
  });
  if (!admin) return null;
  const ask = async () => {
    const reason = await promptDialog({
      title: "Stop every run in flight?",
      body: "Every running run in the organisation stops, on every server, within about 10 seconds. New runs still start: stop a deployment on its page to keep it from taking more.",
      confirm: "Stop all runs", danger: true,
      input: { label: "Reason (kept in the audit log)", placeholder: "e.g. an agent is looping", required: true },
    });
    if (reason && reason.trim()) stop.mutate(reason.trim());
  };
  return (
    <section className="settings-card danger-zone" aria-label="Stop all runs">
      <h2>Stop all runs</h2>
      <p className="muted small">For an emergency: an agent looping, spending, or calling something it should not.</p>
      <button type="button" className="danger" disabled={stop.isPending} onClick={ask}>Stop all runs…</button>
      {stop.isError && <p role="alert" className="error-text small">{(stop.error as Error).message}</p>}
    </section>
  );
}
