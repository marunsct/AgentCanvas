/** The page an Approve or Deny link opens (F-9). Opening it decides nothing: it shows what the link is for, and only
 * the button decides, so a mail scanner or a link preview cannot approve. A link posted to a channel (Slack, a
 * webhook), or one the organisation does not let decide by itself, leads to the item in the Inbox, behind sign-in. */
import { useEffect, useState } from "react";
import { decideByLink, inspectLink, itemOf, type LinkInfo, tokenFromHash } from "./approvalLink";
import { exact } from "../ui/time";

const VERB = { approve: "Approve", deny: "Deny" } as Record<string, string>;
const GONE: Record<string, string> = {
  decided: "This item was already decided, so the link does nothing any more.",
  expired: "This item expired: nobody decided it in time, and its run was cancelled.",
  cancelled: "This item's run was stopped, so there is nothing to decide.",
};

export function ApprovalLinkPage() {
  const [token] = useState(() => tokenFromHash());
  const [info, setInfo] = useState<LinkInfo | null>(null);
  const [error, setError] = useState<string | null>(token ? null : "This address has no link in it: open the link from the message again.");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  useEffect(() => {
    if (!token) return;
    inspectLink(token).then(setInfo, (e: Error) => setError(e.message));
  }, [token]);
  const item = info?.interrupt_id ?? itemOf(token);
  const inbox = item ? `/inbox?item=${encodeURIComponent(item)}` : "/inbox";
  const decide = async () => {
    setBusy(true);
    setError(null);
    try {
      const out = await decideByLink(token, reason.trim() || undefined);
      setDone(out.action === "approve" ? "Approved: the run continues." : "Denied: the run continues without it.");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const verb = VERB[info?.action ?? ""] ?? "Decide";
  return (
    <main className="approve-page" data-testid="approve-page">
      <h1>{info ? `${verb} this?` : "Approval link"}</h1>
      {!info && !error && <p className="muted" role="status">Loading…</p>}
      {info && (
        <>
          <p><b>{info.workflow_name}</b> <span className="muted">(project {info.project_name})</span> is waiting at the
            step <b>“{info.step}”</b>.</p>
          <p className="muted small">Who may decide: {info.deciders}.</p>
          {done ? (
            <p className="callout" role="status" data-testid="approve-done">{done}</p>
          ) : info.status !== "pending" ? (
            <p className="callout" data-testid="approve-gone">{GONE[info.status] ?? `This item is ${info.status}.`}</p>
          ) : info.sign_in ? (
            <>
              <p data-testid="approve-sign-in">To decide, sign in and open it in the Inbox: {info.why_sign_in}.</p>
              <p><a className="button primary" href={inbox}>Open it in the Inbox</a></p>
            </>
          ) : (
            <>
              <p className="small">You decide as <b>{info.recipient}</b>. The link works once, until{" "}
                {exact(info.expires_at)}.</p>
              {info.action === "deny" && info.kind === "tool_approval" && (
                <label>Reason (the agent reads it)
                  <input aria-label="reason" value={reason} maxLength={500} placeholder="Rejected by reviewer"
                    onChange={(e) => setReason(e.target.value)} />
                </label>
              )}
              <div className="row gap">
                <button type="button" className={info.action === "approve" ? "primary" : "danger-solid"} disabled={busy}
                  onClick={() => void decide()}>{busy ? "Deciding…" : verb}</button>
                <a href={inbox}>Open it in the Inbox instead</a>
              </div>
            </>
          )}
        </>
      )}
      {error && (
        <div role="alert">
          <p className="error-text">{error}</p>
          <p><a href={inbox}>Open the Inbox</a></p>
        </div>
      )}
    </main>
  );
}
