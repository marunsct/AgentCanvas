/** Per-user OAuth connections to app connectors (G-7): what the API lists, and starting a sign-in. */
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import type { AuthorizeOut, ConnectionOut } from "../api/generated";

export const CONNECTIONS_KEY = ["connections"];

export function useConnections() {
  return useQuery({ queryKey: CONNECTIONS_KEY, queryFn: () => apiFetch<ConnectionOut[]>("/connections") });
}

/** The address as the API keys connections by it (a URL as a browser writes it, without a fragment); null if it is
 * not an address yet. */
export function resourceOf(url: unknown): string | null {
  if (typeof url !== "string" || !/^https?:\/\//i.test(url.trim())) return null;
  try {
    const u = new URL(url.trim());
    u.hash = "";
    return u.href;
  } catch {
    return null;
  }
}

/** Which of `list` is the caller's own (or the service's) connection to this server. */
export function connectionFor(list: ConnectionOut[] | undefined, url: unknown, identity: "starter" | "service"): ConnectionOut | undefined {
  const resource = resourceOf(url);
  return list?.find((c) => c.resource === resource && (identity === "service" ? c.owner === "service" : c.owner === "you"));
}

/** Send the browser to the server's sign-in; it comes back to /oauth/callback and then to this page. */
export async function connect(serverUrl: string, service: boolean): Promise<void> {
  const out = await apiFetch<AuthorizeOut>("/connections/authorize", {
    method: "POST",
    json: { server_url: serverUrl, service, return_to: location.pathname + location.search },
  });
  location.assign(out.authorization_url);
}
