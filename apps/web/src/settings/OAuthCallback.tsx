/** Where the authorization server sends the browser back (G-7): hands `code` and `state` to the API as the signed-in
 * person, then returns to the page the sign-in started from. */
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router";
import { apiFetch } from "../api/client";
import type { CallbackOut } from "../api/generated";
import { toast } from "../ui/dialog";
import { CONNECTIONS_KEY } from "./connections";

/** What the address carries: the answer, or the server's error. */
export function readCallback(search: string): { code: string; state: string; iss?: string } | { error: string } {
  const q = new URLSearchParams(search);
  if (q.get("error")) return { error: [q.get("error"), q.get("error_description")].filter(Boolean).join(": ") };
  const code = q.get("code"), state = q.get("state");
  if (!code || !state) return { error: "The sign-in page sent back no code. Start again from the connector." };
  return { code, state, ...(q.get("iss") ? { iss: q.get("iss") as string } : {}) };
}

export function OAuthCallback() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false); // StrictMode runs effects twice; a code works once
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const got = readCallback(location.search);
    if ("error" in got) { setError(got.error); return; }
    history.replaceState(null, "", location.pathname); // the code is single-use: do not leave it in the address bar
    apiFetch<CallbackOut>("/connections/callback", { method: "POST", json: got })
      .then((out) => {
        void qc.invalidateQueries({ queryKey: CONNECTIONS_KEY });
        toast(`Connected to ${out.connection.host}`);
        navigate(out.return_to || "/", { replace: true });
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, [navigate, qc]);
  if (error) {
    return (
      <div className="page" role="alert">
        <h2>Could not connect</h2>
        <p className="error-text">{error}</p>
        <Link to="/settings/connections">Back to your connections</Link>
      </div>
    );
  }
  return <div className="page muted" role="status">Finishing the sign-in…</div>;
}
