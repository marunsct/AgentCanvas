/** Holds the app back until sign-in is settled (M7-T1): immediately in dev mode, after the OIDC redirect otherwise. */
import { useEffect, useState, type ReactNode } from "react";
import { fetchAuthConfig, initAuth } from "../api/auth";

export function AuthGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<"pending" | "ready" | Error>("pending");
  useEffect(() => {
    let live = true;
    fetchAuthConfig()
      .then(initAuth)
      .then((back) => {
        if (back) history.replaceState(null, "", back); // leave the callback URL before the router reads it
        if (live) setState("ready");
      })
      .catch((e: unknown) => { if (live) setState(e instanceof Error ? e : new Error(String(e))); });
    return () => { live = false; };
  }, []);
  if (state === "ready") return <>{children}</>;
  if (state === "pending") return <div className="page muted" role="status">Signing in…</div>;
  return (
    <div className="page" role="alert">
      <h2>Could not sign in</h2>
      <p className="error-text">{state.message}</p>
      <button type="button" onClick={() => location.assign("/")}>Try again</button>
    </div>
  );
}
