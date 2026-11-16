"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import CountdownTimer from "./CountdownTimer";
import LogoutLink from "./LogoutLink";
import Button from "./ui/Button";
import Envelope from "./ui/Envelope";
import PaperCard from "./ui/PaperCard";
import { ApiError, getMe, type Me } from "@/lib/api";
import { formatBudget, formatExchangeTime } from "@/lib/format";
import { clearToken, getToken } from "@/lib/session";
import { useIdleSession } from "@/lib/useIdleSession";
import styles from "./CountdownScreen.module.css";

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
      <Envelope action={<LogoutLink />}>
        <p role="status" className={styles.status}>
          Učitavam…
        </p>
      </Envelope>
    );
  }

  if (state.kind === "error") {
    return (
      <Envelope action={<LogoutLink />}>
        <div className={styles.status}>
          <p role="alert" className={styles.error}>
            Ne mogu učitati podatke. Pokušaj ponovno.
          </p>
          <Button onClick={retry}>Pokušaj ponovno</Button>
        </div>
      </Envelope>
    );
  }

  const { me } = state;
  const { event } = me;
  return (
    <Envelope action={<LogoutLink />}>
      <div className={styles.layout}>
        <h1 className={styles.title}>Bok, {me.vocative_name}!</h1>

        {me.personal_note && (
          <PaperCard variant="note" label="Poruka" className={styles.note}>
            <p>{me.personal_note}</p>
          </PaperCard>
        )}

        <PaperCard label="Pozivnica" stamp className={styles.invitation}>
          <h2>{event.name}</h2>
          <dl className={styles.details}>
            <div>
              <dt className="label">Mjesto</dt>
              <dd>{event.place}</dd>
            </div>
            <div>
              <dt className="label">Kada</dt>
              <dd>{formatExchangeTime(event.exchange_at)}</dd>
            </div>
          </dl>
          {event.budget_amount !== null && (
            <small className={styles.budget}>
              Proračun: {formatBudget(event.budget_amount, event.budget_currency)}
            </small>
          )}
        </PaperCard>

        <div className={styles.timer}>
          <CountdownTimer
            exchangeAt={event.exchange_at}
            afterEventMessage={event.after_event_message}
          />
        </div>

        <div className={styles.action}>
          {me.drawn ? (
            <Button onClick={() => router.push("/reveal/")}>Otvori svoj poklon</Button>
          ) : (
            <p className={styles.notDrawn}>Izvlačenje još nije održano. Navrati malo kasnije.</p>
          )}
        </div>
      </div>
    </Envelope>
  );
}
