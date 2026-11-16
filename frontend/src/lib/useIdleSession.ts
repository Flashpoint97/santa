"use client";

import { useEffect, useRef } from "react";
import { ApiError, heartbeat, logout } from "./api";
import { clearToken, getToken } from "./session";

/** Local idle limit. The server enforces its own (authoritative) one; this just mirrors it. */
export const IDLE_TIMEOUT_MS = 10 * 60 * 1000;
/** At most one heartbeat per this interval while the user is active. */
export const HEARTBEAT_INTERVAL_MS = 60 * 1000;

const ACTIVITY_EVENTS = ["pointerdown", "touchstart", "keydown", "scroll", "wheel"] as const;

/**
 * Keeps the session alive while the user is active and ends it when they are not.
 *
 * - Taps, scrolls and key presses count as activity.
 * - While active, sends a heartbeat at most once a minute.
 * - After 10 minutes without activity (also checked when the tab becomes visible again,
 *   because phones pause timers in the background) the token is cleared, a best-effort
 *   logout is sent, and `onExpired` runs.
 * - A 401 from a heartbeat means the server already ended the session: same cleanup.
 */
export function useIdleSession(onExpired: () => void): void {
  // Keep the latest callback without re-subscribing every listener on each render.
  const onExpiredRef = useRef(onExpired);
  useEffect(() => {
    onExpiredRef.current = onExpired;
  }, [onExpired]);

  useEffect(() => {
    if (!getToken()) return;

    let lastActivity = Date.now();
    let lastHeartbeat = lastActivity; // the page load just made an authenticated request
    let idleTimer: number | undefined;
    let done = false;

    function expire(notifyServer: boolean) {
      if (done) return;
      done = true;
      const token = getToken();
      clearToken();
      if (notifyServer && token) {
        // Best effort: the server drops idle sessions itself, so a failure changes nothing.
        logout(token).catch(() => undefined);
      }
      onExpiredRef.current();
    }

    function scheduleIdleCheck() {
      window.clearTimeout(idleTimer);
      const remaining = IDLE_TIMEOUT_MS - (Date.now() - lastActivity);
      idleTimer = window.setTimeout(checkIdle, Math.max(remaining, 0));
    }

    function checkIdle() {
      if (Date.now() - lastActivity >= IDLE_TIMEOUT_MS) {
        expire(true);
      } else {
        scheduleIdleCheck();
      }
    }

    function sendHeartbeat() {
      const token = getToken();
      if (!token) {
        expire(false);
        return;
      }
      heartbeat(token).catch((error: unknown) => {
        if (error instanceof ApiError && error.status === 401) expire(false);
        // Any other failure (e.g. a dropped connection) is retried on the next activity.
      });
    }

    function onActivity() {
      if (done) return;
      const now = Date.now();
      // A tap after the idle limit must not revive the session, even if the timer was
      // delayed (suspended tab): the server would reject it too.
      if (now - lastActivity >= IDLE_TIMEOUT_MS) {
        expire(true);
        return;
      }
      lastActivity = now;
      scheduleIdleCheck();
      if (now - lastHeartbeat >= HEARTBEAT_INTERVAL_MS) {
        lastHeartbeat = now;
        sendHeartbeat();
      }
    }

    function onVisibilityChange() {
      if (document.visibilityState === "visible") checkIdle();
    }

    for (const name of ACTIVITY_EVENTS) {
      window.addEventListener(name, onActivity, { passive: true, capture: true });
    }
    document.addEventListener("visibilitychange", onVisibilityChange);
    scheduleIdleCheck();

    return () => {
      done = true;
      window.clearTimeout(idleTimer);
      for (const name of ACTIVITY_EVENTS) {
        window.removeEventListener(name, onActivity, { capture: true });
      }
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, []);
}
