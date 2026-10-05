/** Holds the app back until sign-in is settled (M7-T1: at once in dev mode, after the OIDC redirect otherwise)
 * and the session's project is chosen (M7-T2). */
import { useEffect, useState, type ReactNode } from "react";
import { fetchAuthConfig, initAuth, signInEnabled, switchAccount } from "../api/auth";
import { chooseProject } from "../api/queries";
import { loadMe } from "../api/session";

// one start-up per page load: StrictMode runs effects twice in development, and a second redirect callback would
// find its sign-in state already consumed
let startup: Promise<void> | null = null;

function start(): Promise<void> {
  startup ??= fetchAuthConfig()
    .then(initAuth)
    .then((back) => {
      if (back) history.replaceState(null, "", back); // leave the callback URL before the router reads it
      return loadMe();
    })
    .then(async () => { await chooseProject(); });
  return startup;
}

export function AuthGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<"pending" | "ready" | Error>("pending");
  useEffect(() => {
    let live = true;
    start()
      .then(() => { if (live) setState("ready"); })
      .catch((e: unknown) => { if (live) setState(e instanceof Error ? e : new Error(String(e))); });
    return () => { live = false; };
  }, []);
  if (state === "ready") return <>{children}</>;
  if (state === "pending") return <div className="page muted" role="status">Signing in…</div>;
  return (
    <div className="page" role="alert">
      <h2>Could not sign in</h2>
      <p className="error-text">{state.message}</p>
      <div className="row gap">
        <button type="button" onClick={() => location.assign("/")}>Try again</button>
        {signInEnabled() && <button type="button" onClick={() => void switchAccount()}>Sign in with a different account</button>}
      </div>
    </div>
  );
}
