// Typed client for the backend API. Matches the contract in ticket 02.
import { API_URL } from "./config";

const API_PREFIX = "/api/v1";

export type SessionResponse = {
  token: string;
  idle_timeout_seconds: number;
};

export type EventInfo = {
  name: string;
  /** Decimal string such as "50.00", or null. */
  budget_amount: string | null;
  budget_currency: string;
  /** ISO-8601, UTC. */
  exchange_at: string;
  place: string;
  after_event_message: string;
};

export type Me = {
  display_name: string;
  vocative_name: string;
  personal_note: string | null;
  event: EventInfo;
  drawn: boolean;
  has_revealed: boolean;
};

/** The server answered with a non-2xx status. */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number) {
    super(`API request failed with status ${status}`);
    this.name = "ApiError";
    this.status = status;
  }
}

/** The server could not be reached at all. */
export class NetworkError extends Error {
  constructor() {
    super("Network request failed");
    this.name = "NetworkError";
  }
}

async function send(path: string, init: RequestInit): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}${API_PREFIX}${path}`, init);
  } catch {
    throw new NetworkError();
  }
  if (!response.ok) {
    throw new ApiError(response.status);
  }
  return response;
}

/** For endpoints that answer with a JSON body. */
async function request<T>(path: string, init: RequestInit): Promise<T> {
  return (await (await send(path, init)).json()) as T;
}

/** For endpoints that answer 204 No Content (parsing an empty body as JSON would throw). */
async function requestNoBody(path: string, init: RequestInit): Promise<void> {
  await send(path, init);
}

/** POST /session. 400 = invalid code, 422 = malformed, 429 = too many attempts. */
export function login(code: string): Promise<SessionResponse> {
  return request<SessionResponse>("/session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code }),
  });
}

/** GET /me. 401 = missing, unknown or expired token. */
export function getMe(token: string): Promise<Me> {
  return request<Me>("/me", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

/** POST /session/heartbeat (204). Marks the session as active. 401 = expired or unknown. */
export function heartbeat(token: string): Promise<void> {
  return requestNoBody("/session/heartbeat", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
}

/** DELETE /session (204). Ends this session. 401 = it was already gone. */
export function logout(token: string): Promise<void> {
  return requestNoBody("/session", {
    method: "DELETE",
    headers: { Authorization: `Bearer ${token}` },
  });
}
