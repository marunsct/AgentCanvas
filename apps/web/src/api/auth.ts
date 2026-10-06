/** Sign-in (M7-T1): OIDC authorization code + PKCE with oidc-client-ts, or nothing in the API's dev mode.
 *
 * The API says which at `GET /v1/auth/config`. oidc-client-ts is loaded only when sign-in is on, so the
 * dev build's entry bundle does not grow. */
import type { User, UserManager } from "oidc-client-ts";

export interface AuthConfig {
  mode: "dev" | "oidc";
  issuer?: string | null;
  client_id?: string | null;
  scopes?: string | null;
  audience?: string | null;
}

export const CALLBACK_PATH = "/auth/callback";

let manager: UserManager | null = null;
let user: User | null = null;

/** The bearer token for API calls, or null in dev mode / before sign-in. */
export function accessToken(): string | null {
  return user && !user.expired ? user.access_token : null;
}

/** Send the browser to the identity provider, coming back to the current page afterwards. */
export async function signIn(): Promise<void> {
  if (!manager) return;
  await manager.signinRedirect({ state: location.pathname + location.search });
}

/** Whether this page signs in with an identity provider (false in the API's dev mode). */
export function signInEnabled(): boolean {
  return manager !== null;
}

/** Renew the token without leaving the page (hidden iframe or refresh token); true when it worked. */
export async function renew(): Promise<boolean> {
  if (!manager) return false;
  try {
    user = await manager.signinSilent();
    return !!user && !user.expired;
  } catch {
    return false;
  }
}

const LAST_REDIRECT = "agentcanvas.signin.redirected_at";

/** The API refused our token and renewing did not help: sign in again, unless we did so moments ago. A token the
 * identity provider just issued being refused means a configuration problem (audience, issuer, clock, keys), and
 * redirecting again would loop forever; the caller shows the error instead. Returns whether it redirected. */
export function signInAgain(): boolean {
  if (!manager) return false;
  let last = 0;
  try { last = Number(sessionStorage.getItem(LAST_REDIRECT) ?? 0); } catch { /* storage blocked */ }
  if (Date.now() - last < 60_000) return false;
  try { sessionStorage.setItem(LAST_REDIRECT, String(Date.now())); } catch { /* storage blocked */ }
  void signIn();
  return true;
}

/** Sign out at the identity provider; providers without an end-session endpoint just forget the local session. */
export async function signOut(): Promise<void> {
  if (!manager) return;
  try {
    await manager.signoutRedirect();
  } catch {
    await manager.removeUser();
    location.assign("/");
  }
}

/** Forget this account and let the user pick another at the identity provider. */
export async function switchAccount(): Promise<void> {
  if (!manager) return;
  await manager.removeUser();
  await manager.signinRedirect({ state: "/", prompt: "select_account" });
}

export async function fetchAuthConfig(): Promise<AuthConfig> {
  const res = await fetch("/api/v1/auth/config");
  if (!res.ok) throw new Error(`cannot load the sign-in configuration (HTTP ${res.status})`);
  return (await res.json()) as AuthConfig;
}

/** Start sign-in for this page load. Resolves to where the app should be (the callback restores the page the
 * user was on); never resolves while the browser is being redirected to the identity provider. */
export async function initAuth(config: AuthConfig): Promise<string | null> {
  if (config.mode !== "oidc") return null;
  if (!config.issuer || !config.client_id) throw new Error("sign-in is on but the API has no OIDC client configured");
  const { UserManager, WebStorageStateStore } = await import("oidc-client-ts");
  manager = new UserManager({
    authority: config.issuer,
    client_id: config.client_id,
    redirect_uri: `${location.origin}${CALLBACK_PATH}`,
    post_logout_redirect_uri: location.origin,
    response_type: "code", // PKCE is on by default for the code flow
    scope: config.scopes ?? "openid profile email offline_access", // offline_access: a refresh token renews silently
    extraQueryParams: config.audience ? { audience: config.audience } : {},
    automaticSilentRenew: true,
    userStore: new WebStorageStateStore({ store: window.sessionStorage }),
  });
  manager.events.addUserLoaded((u) => { user = u; });
  manager.events.addUserUnloaded(() => { user = null; });
  if (location.pathname === CALLBACK_PATH) {
    // the redirect back from sign-in, or the hidden iframe of a silent renew: signinCallback tells them apart
    const signedIn = await manager.signinCallback();
    if (!signedIn) return new Promise<never>(() => {}); // silent renew: the parent window picks up the new token
    user = signedIn;
    return typeof user.state === "string" && user.state.startsWith("/") ? user.state : "/";
  }
  user = await manager.getUser();
  if (!user || user.expired) {
    await signIn();
    return new Promise<never>(() => {}); // the page is navigating away
  }
  return null;
}
