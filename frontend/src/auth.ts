const domain = import.meta.env.VITE_COGNITO_DOMAIN?.replace(/\/$/, "");
const clientId = import.meta.env.VITE_COGNITO_CLIENT_ID;
const redirectUri = import.meta.env.VITE_COGNITO_REDIRECT_URI || `${window.location.origin}/auth/callback`;
const logoutUri = import.meta.env.VITE_COGNITO_LOGOUT_URI || window.location.origin;
const ACCESS_TOKEN_KEY = "abos_access_token";
const VERIFIER_KEY = "abos_pkce_verifier";
const STATE_KEY = "abos_oauth_state";

export const authConfigured = Boolean(domain && clientId);

export function accessToken(): string | null {
  return sessionStorage.getItem(ACCESS_TOKEN_KEY);
}

function base64Url(bytes: Uint8Array): string {
  let binary = "";
  bytes.forEach((byte) => { binary += String.fromCharCode(byte); });
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function sha256(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return base64Url(new Uint8Array(digest));
}

export async function beginLogin(): Promise<void> {
  if (!authConfigured || !domain || !clientId) return;
  const random = new Uint8Array(32);
  crypto.getRandomValues(random);
  const verifier = base64Url(random);
  const stateBytes = new Uint8Array(24);
  crypto.getRandomValues(stateBytes);
  const state = base64Url(stateBytes);
  sessionStorage.setItem(VERIFIER_KEY, verifier);
  sessionStorage.setItem(STATE_KEY, state);
  const query = new URLSearchParams({
    response_type: "code", client_id: clientId, redirect_uri: redirectUri,
    scope: "openid email", state, code_challenge: await sha256(verifier),
    code_challenge_method: "S256",
  });
  window.location.assign(`${domain}/oauth2/authorize?${query.toString()}`);
}

export async function completeLogin(): Promise<string | null> {
  if (!authConfigured || !domain || !clientId) return null;
  const params = new URLSearchParams(window.location.search);
  const code = params.get("code");
  if (!code) return accessToken();
  const expectedState = sessionStorage.getItem(STATE_KEY);
  const verifier = sessionStorage.getItem(VERIFIER_KEY);
  if (!expectedState || params.get("state") !== expectedState || !verifier) {
    throw new Error("Sign-in verification failed. Please sign in again.");
  }
  const body = new URLSearchParams({
    grant_type: "authorization_code", client_id: clientId, code,
    redirect_uri: redirectUri, code_verifier: verifier,
  });
  const response = await fetch(`${domain}/oauth2/token`, {
    method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body,
  });
  const tokens = await response.json();
  if (!response.ok || !tokens.access_token) {
    throw new Error(tokens.error_description || "Could not complete sign-in.");
  }
  sessionStorage.setItem(ACCESS_TOKEN_KEY, tokens.access_token);
  if (tokens.refresh_token) sessionStorage.setItem("abos_refresh_token", tokens.refresh_token);
  sessionStorage.removeItem(VERIFIER_KEY);
  sessionStorage.removeItem(STATE_KEY);
  window.history.replaceState({}, document.title, window.location.pathname);
  return tokens.access_token as string;
}

export async function apiFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const send = (token: string | null) => {
    const headers = new Headers(init.headers);
    if (authConfigured && token) headers.set("Authorization", `Bearer ${token}`);
    return fetch(input, { ...init, headers });
  };
  let response = await send(accessToken());
  const refreshToken = sessionStorage.getItem("abos_refresh_token");
  if (authConfigured && response.status === 401 && domain && clientId && refreshToken) {
    const body = new URLSearchParams({ grant_type: "refresh_token", client_id: clientId, refresh_token: refreshToken });
    const refreshed = await fetch(`${domain}/oauth2/token`, {
      method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body,
    });
    const tokens = await refreshed.json();
    if (refreshed.ok && tokens.access_token) {
      sessionStorage.setItem(ACCESS_TOKEN_KEY, tokens.access_token);
      response = await send(tokens.access_token as string);
    }
  }
  return response;
}

export function signOut(): void {
  sessionStorage.removeItem(ACCESS_TOKEN_KEY);
  sessionStorage.removeItem("abos_refresh_token");
  if (authConfigured && domain && clientId) {
    const query = new URLSearchParams({ client_id: clientId, logout_uri: logoutUri });
    window.location.assign(`${domain}/logout?${query.toString()}`);
  } else {
    window.location.reload();
  }
}
