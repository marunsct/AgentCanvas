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

export async function signOut(): Promise<void> {
  if (!manager) return;
  await manager.signoutRedirect();
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
    scope: config.scopes ?? "openid profile email",
    extraQueryParams: config.audience ? { audience: config.audience } : {},
    automaticSilentRenew: true,
    userStore: new WebStorageStateStore({ store: window.sessionStorage }),
  });
  manager.events.addUserLoaded((u) => { user = u; });
  manager.events.addUserUnloaded(() => { user = null; });
  if (location.pathname === CALLBACK_PATH) {
    user = await manager.signinRedirectCallback();
    const back = typeof user.state === "string" && user.state.startsWith("/") ? user.state : "/";
    return back;
  }
  user = await manager.getUser();
  if (!user || user.expired) {
    await signIn();
    return new Promise<never>(() => {}); // the page is navigating away
  }
  return null;
}
