/** Who may decide a pause, and why you may not (E-6): the same lines in the Inbox and in the editor's Run tab. */
import type { InboxItem } from "../api/queries";

export function WhoDecides({ item }: { item: Pick<InboxItem, "deciders" | "may_decide" | "why_not" | "kind"> }) {
  return (
    <>
      {item.deciders && <p className="small muted" data-testid="inbox-deciders">Decided by: {item.deciders}</p>}
      {item.may_decide === false && item.why_not && item.kind !== "timer" && (
        <p className="small muted" data-testid="inbox-why-not">You cannot decide this one: {item.why_not}.</p>
      )}
    </>
  );
}
