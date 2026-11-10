/** Settings › Organisation › Notifications (E-6): who is told that an approval or a question waits. E-mail goes
 * through the server's mail server to the people who may decide; a Slack incoming webhook and a signed webhook of the
 * organisation's own get every item. The Slack address and the signing secret are never shown again. Admins only. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { apiFetch } from "../api/client";
import type { NotificationsOut, NotificationsSaved, TestResult } from "../api/generated";
import { toast } from "../ui/dialog";
import { QueryStatus } from "../ui/QueryStatus";

const CHANNEL = { email: "E-mail", slack: "Slack", webhook: "Webhook" } as Record<string, string>;

export function NotificationSettings() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["org-notifications"], queryFn: () => apiFetch<NotificationsOut>("/org/notifications") });
  const [email, setEmail] = useState(false);
  const [to, setTo] = useState("");
  const [slack, setSlack] = useState("");
  const [hook, setHook] = useState("");
  const [digest, setDigest] = useState(false);
  const [actions, setActions] = useState(false);
  const [secret, setSecret] = useState<string | null>(null);
  const [tested, setTested] = useState<TestResult[] | null>(null);
  useEffect(() => {
    if (!q.data) return;
    setEmail(q.data.email.enabled);
    setTo(q.data.email.to.join(", "));
    setHook(q.data.webhook_url ?? "");
    setDigest(Boolean(q.data.digest));
    setActions(Boolean(q.data.actions));
  }, [q.data]);
  const save = useMutation({
    mutationFn: (json: Record<string, unknown>) => apiFetch<NotificationsSaved>("/org/notifications", { method: "PUT", json }),
    onSuccess: (out) => {
      qc.setQueryData(["org-notifications"], out);
      setSlack("");
      if (out.webhook_secret) setSecret(out.webhook_secret);
      toast("Saved where notifications go");
    },
  });
  const test = useMutation({
    mutationFn: () => apiFetch<TestResult[]>("/org/notifications/test", { method: "POST" }),
    onSuccess: setTested,
  });
  if (!q.data) return <section className="settings-card"><h2>Notifications</h2><QueryStatus q={q} what="the notification settings" /></section>;
  const d = q.data;
  const addresses = to.split(/[\s,;]+/).filter(Boolean);
  const changed: Record<string, unknown> = {
    ...(email !== d.email.enabled ? { email } : {}),
    ...(addresses.join(",") !== d.email.to.join(",") ? { email_to: addresses } : {}),
    ...(slack.trim() ? { slack_url: slack.trim() } : {}),
    ...(hook.trim() !== (d.webhook_url ?? "") ? { webhook_url: hook.trim() || null } : {}),
    ...(digest !== Boolean(d.digest) ? { digest } : {}),
    ...(actions !== Boolean(d.actions) ? { actions } : {}),
  };
  return (
    <section className="settings-card" aria-label="Notifications">
      <h2>Notifications</h2>
      <p className="muted small">
        Tell people when an approval or a question waits in the Inbox, and when it expires. Messages name the workflow,
        the step and who may decide, with a link to the Inbox; never what the run asks. Draft runs you start in the
        editor are not announced unless their step names who decides them.
      </p>

      <h3>E-mail</h3>
      {d.email.available ? (
        <>
          <label className="row"><input type="checkbox" checked={email} onChange={(e) => setEmail(e.target.checked)} aria-label="send e-mail" />
            E-mail the people who may decide</label>
          <label>Addresses for steps that name nobody (a team mailbox); empty: every reviewer, builder and admin
            <input aria-label="e-mail addresses" value={to} placeholder="approvals@example.com" disabled={!email}
              onChange={(e) => setTo(e.target.value)} />
          </label>
        </>
      ) : (
        <p className="muted small" data-testid="no-smtp">This server has no mail server. Its operator sets <code>AGENTCANVAS_SMTP_HOST</code> and <code>AGENTCANVAS_SMTP_FROM</code>.</p>
      )}

      <h3>Slack</h3>
      <p className="muted small">
        {d.slack ? "A Slack incoming webhook is set. Paste a new address to replace it." : "Paste a Slack incoming webhook address to post every waiting item to a channel."}
      </p>
      <div className="row gap">
        <input aria-label="Slack webhook address" type="password" autoComplete="off" value={slack}
          placeholder="https://hooks.slack.com/services/…" onChange={(e) => setSlack(e.target.value)} />
        {d.slack && <button type="button" className="link small" disabled={save.isPending}
          onClick={() => save.mutate({ slack_url: null })}>Remove</button>}
      </div>

      <h3>Webhook</h3>
      <p className="muted small">
        A JSON POST for every waiting item, signed like webhook triggers: <code>X-AgentCanvas-Signature: sha256=…</code> over{" "}
        <code>&lt;X-AgentCanvas-Timestamp&gt;.&lt;body&gt;</code>.
        {d.webhook_secret_set ? " Its signing secret is set." : " A signing secret is made when you save an address."}
      </p>
      <input aria-label="webhook address" value={hook} placeholder="https://example.com/approvals" onChange={(e) => setHook(e.target.value)} />
      {secret && (
        <div className="callout" role="status" aria-label="webhook signing secret">
          <p><strong>Copy the signing secret now.</strong> It is not shown again.</p>
          <div className="row gap">
            <code className="mono" data-testid="webhook-secret">{secret}</code>
            <button type="button" onClick={() => void navigator.clipboard?.writeText(secret).then(() => toast("Copied"))}>Copy</button>
            <button type="button" className="link small" onClick={() => setSecret(null)}>Done</button>
          </div>
        </div>
      )}

      <h3>Approve and Deny in messages</h3>
      <label className="row"><input type="checkbox" checked={actions} onChange={(e) => setActions(e.target.checked)} aria-label="approve and deny links" />
        Add Approve and Deny links to approval messages</label>
      <p className="muted small">
        Each person gets their own e-mail, with links that decide as them after the Inbox's checks: they work once, for a
        week at most, and open a page that asks to confirm (so a mail scanner cannot approve). Slack gets buttons and the
        webhook <code>actions</code>; those open the item in the Inbox, where whoever decides signs in. With multi-factor
        sign-in required, every link opens the Inbox. Questions are always answered in the Inbox.
      </p>

      <h3>Weekly run digests</h3>
      <label className="row"><input type="checkbox" checked={digest} onChange={(e) => setDigest(e.target.checked)} aria-label="send weekly run digests" />
        Send each project's weekly run digest here too</label>
      <p className="muted small">
        Each Monday: last week's runs, failures, spend and latency per project, with a link to the Runs page. Counts and
        workflow names only, never the errors' text or what a run was asked. E-mail goes to the addresses above, else to
        every admin and builder.
      </p>

      <div className="row gap">
        <button type="button" className="primary" disabled={save.isPending || !Object.keys(changed).length}
          onClick={() => save.mutate(changed)}>Save</button>
        <button type="button" disabled={test.isPending || !(d.email.enabled || d.slack || d.webhook_url)}
          onClick={() => test.mutate()}>Send a test</button>
      </div>
      {save.error && <p className="error-text small" role="alert">{(save.error as Error).message}</p>}
      {tested && (
        <ul className="small" aria-label="test results">
          {tested.map((r) => <li key={r.channel}>{CHANNEL[r.channel] ?? r.channel}: {r.ok ? "sent" : `failed: ${r.error}`}</li>)}
        </ul>
      )}
    </section>
  );
}
