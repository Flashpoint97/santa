// The session token lives in sessionStorage (never localStorage), so closing
// the tab logs the participant out.
const TOKEN_KEY = "santa.session-token";

export function getToken(): string | null {
  try {
    return window.sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string): void {
  try {
    window.sessionStorage.setItem(TOKEN_KEY, token);
  } catch {
    // Storage unavailable (e.g. blocked): the user simply has to log in again.
  }
}

export function clearToken(): void {
  try {
    window.sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    // Nothing to clear.
  }
}
