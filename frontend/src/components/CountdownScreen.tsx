"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import CountdownTimer from "./CountdownTimer";
import { ApiError, getMe, type Me } from "@/lib/api";
import { formatBudget, formatExchangeTime } from "@/lib/format";
import { clearToken, getToken } from "@/lib/session";
import { useIdleSession } from "@/lib/useIdleSession";

type State =
  | { kind: "loading" }
  | { kind: "error" }
  | { kind: "ready"; me: Me };

export default function CountdownScreen() {
  const router = useRouter();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);

  // Heartbeat while active; after 10 idle minutes clear the token and go back to the Code screen.
  const backToCode = useCallback(() => router.replace("/"), [router]);
  useIdleSession(backToCode);

  useEffect(() => {
    const token = getToken();
    if (!token) {
      router.replace("/");
      return;
    }
    let cancelled = false;
    getMe(token).then(
      (me) => {
        if (!cancelled) setState({ kind: "ready", me });
      },
      (error: unknown) => {
        if (cancelled) return;
        if (error instanceof ApiError && error.status === 401) {
          clearToken();
          router.replace("/");
          return;
        }
        setState({ kind: "error" });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [router, attempt]);

  const retry = useCallback(() => {
    setState({ kind: "loading" });
    setAttempt((n) => n + 1);
  }, []);

  if (state.kind === "loading") {
    return (
      <main>
        <p>Učitavam…</p>
      </main>
    );
  }

  if (state.kind === "error") {
    return (
      <main>
        <p role="alert">Ne mogu učitati podatke. Pokušaj ponovno.</p>
        <button type="button" onClick={retry}>
          Pokušaj ponovno
        </button>
      </main>
    );
  }

  const { me } = state;
  const { event } = me;
  return (
    <main>
      <h1>Bok, {me.vocative_name}!</h1>
      {me.personal_note && <p>{me.personal_note}</p>}

      <h2>{event.name}</h2>
      <ul>
        {event.budget_amount !== null && (
          <li>Proračun: {formatBudget(event.budget_amount, event.budget_currency)}</li>
        )}
        <li>Mjesto: {event.place}</li>
        <li>Kada: {formatExchangeTime(event.exchange_at)}</li>
      </ul>

      <CountdownTimer
        exchangeAt={event.exchange_at}
        afterEventMessage={event.after_event_message}
      />

      {!me.drawn && <p>Izvlačenje još nije održano. Navrati malo kasnije.</p>}
    </main>
  );
}
