// Cold-start handling. The free backend host sleeps after ~15 idle minutes, and the
// first request then takes up to a minute. We poll GET /health (at the API root, not
// under /api/v1) with backoff until it answers 200.
import { API_URL } from "./config";

/** A warm server answering within this time never shows the wake screen. */
export const GRACE_MS = 1500;
/** Give up (and offer a retry button) after this long. */
export const POLL_TIMEOUT_MS = 90_000;
/** One /health request may hang while the host boots; cut it off after this long. */
const REQUEST_TIMEOUT_MS = 8000;
/** Once the server answered, don't ask again for this long (page-to-page navigation). */
const AWAKE_TTL_MS = 5 * 60_000;

/** Delay before poll number `attempt + 1`: 2 s, 4 s, 6 s, 8 s, then 10 s. */
export function backoffDelay(attempt: number): number {
  return Math.min(2000 * (attempt + 1), 10_000);
}

type WaitOptions = {
  /** Resolves true when the server is healthy; false (or rejects) when it is not. */
  check: () => Promise<boolean>;
  sleep: (ms: number) => Promise<void>;
  now: () => number;
  /** Polling stops (and the result is false) once this returns true. */
  cancelled: () => boolean;
  timeoutMs?: number;
};

/** Polls `check` with backoff. True = healthy, false = timed out or cancelled. */
export async function waitForBackend(options: WaitOptions): Promise<boolean> {
  const { check, sleep, now, cancelled, timeoutMs = POLL_TIMEOUT_MS } = options;
  const start = now();
  for (let attempt = 0; ; attempt++) {
    let healthy = false;
    try {
      healthy = await check();
    } catch {
      healthy = false;
    }
    if (healthy) return true;
    if (cancelled()) return false;
    const delay = backoffDelay(attempt);
    if (now() - start + delay >= timeoutMs) return false;
    await sleep(delay);
    if (cancelled()) return false;
  }
}

/** One GET /health. 200 = up; 503 (database down), network errors and timeouts = not yet. */
export async function checkHealth(): Promise<boolean> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch(`${API_URL}/health`, {
      cache: "no-store",
      signal: controller.signal,
    });
    return response.status === 200;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

let awakeSince: number | null = null;

export function markAwake(): void {
  awakeSince = Date.now();
}

export function isRecentlyAwake(): boolean {
  return awakeSince !== null && Date.now() - awakeSince < AWAKE_TTL_MS;
}

export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
