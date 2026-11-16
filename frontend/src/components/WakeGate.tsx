"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";
import {
  checkHealth,
  GRACE_MS,
  isRecentlyAwake,
  markAwake,
  sleep,
  waitForBackend,
} from "@/lib/wake";
import styles from "./WakeGate.module.css";
import Button from "./ui/Button";
import Envelope from "./ui/Envelope";
import Stamp from "./ui/Stamp";

type Phase = "checking" | "waking" | "ready" | "timeout";

/**
 * Backend readiness. "checking" is the short grace period (nothing is shown, so a warm
 * server never flashes the wake screen), "waking" is the friendly waiting screen.
 */
export function useBackendReady(): { phase: Phase; retry: () => void } {
  const [phase, setPhase] = useState<Phase>(() => (isRecentlyAwake() ? "ready" : "checking"));
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (isRecentlyAwake()) return;
    let cancelled = false;
    const graceTimer = setTimeout(() => {
      if (!cancelled) setPhase((p) => (p === "checking" ? "waking" : p));
    }, GRACE_MS);

    waitForBackend({
      check: checkHealth,
      sleep,
      now: Date.now,
      cancelled: () => cancelled,
    }).then((healthy) => {
      if (cancelled) return;
      clearTimeout(graceTimer);
      if (healthy) markAwake();
      setPhase(healthy ? "ready" : "timeout");
    });

    return () => {
      cancelled = true;
      clearTimeout(graceTimer);
    };
  }, [attempt]);

  const retry = useCallback(() => {
    setPhase("waking");
    setAttempt((n) => n + 1);
  }, []);

  return { phase, retry };
}

/** Renders `children` only once the backend answers /health; wrap entry screens in it. */
export default function WakeGate({ children }: { children: ReactNode }) {
  const { phase, retry } = useBackendReady();

  if (phase === "ready") return <>{children}</>;
  if (phase === "checking") return <main aria-busy="true" />;

  if (phase === "timeout") {
    return (
      <Envelope>
        <div className={styles.wake}>
          <Stamp large />
          <h1>Patuljci još spavaju</h1>
          <p role="alert">Poslužitelj se nije probudio. Pokušaj ponovno za nekoliko trenutaka.</p>
          <Button onClick={retry}>Pokušaj ponovno</Button>
        </div>
      </Envelope>
    );
  }

  return (
    <Envelope>
      <div className={styles.wake}>
        <Stamp large wobble />
        <h1>Budimo patuljke…</h1>
        <p role="status">Ovo može potrajati do minutu.</p>
      </div>
    </Envelope>
  );
}
