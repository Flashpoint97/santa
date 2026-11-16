"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import LogoutLink from "@/components/LogoutLink";
import RevealAnimation from "@/components/RevealAnimation";
import Button from "@/components/ui/Button";
import Envelope from "@/components/ui/Envelope";
import TextLink from "@/components/ui/TextLink";
import { ApiError, getMe, reveal, type Reveal } from "@/lib/api";
import { revealMessage } from "@/lib/revealMessages";
import { clearToken, getToken } from "@/lib/session";
import { useIdleSession } from "@/lib/useIdleSession";
import styles from "./RevealScreen.module.css";

type Loaded = { vocativeName: string; reveal: Reveal };

type State =
  | { kind: "loading" }
  | { kind: "error" }
  | { kind: "not-drawn" }
  | ({ kind: "ready" } & Loaded);

export default function RevealScreen() {
  const router = useRouter();
  const backToCode = useCallback(() => router.replace("/"), [router]);
  useIdleSession(backToCode);
  const [state, setState] = useState<State>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);
  // Revealing has a server-side side effect (it records the first reveal), so the request is made
  // once per mount even if React re-runs the effect (StrictMode in dev). A failure clears it
  // so that "Pokušaj ponovno" can ask again.
  const request = useRef<Promise<Loaded> | null>(null);

  useEffect(() => {
    const token = getToken();
    if (!token) {
      router.replace("/");
      return;
    }
    let cancelled = false;
    request.current ??= Promise.all([getMe(token), reveal(token)]).then(([me, result]) => ({
      vocativeName: me.vocative_name,
      reveal: result,
    }));
    request.current.then(
      (loaded) => {
        if (!cancelled) setState({ kind: "ready", ...loaded });
      },
      (error: unknown) => {
        request.current = null;
        if (cancelled) return;
        if (error instanceof ApiError && error.status === 401) {
          clearToken();
          router.replace("/");
          return;
        }
        if (error instanceof ApiError && error.status === 404) {
          setState({ kind: "not-drawn" });
          return;
        }
        setState({ kind: "error" });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [router, attempt]);

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
            Ne mogu otvoriti poklon. Pokušaj ponovno.
          </p>
          <Button
            onClick={() => {
              setState({ kind: "loading" });
              setAttempt((n) => n + 1);
            }}
          >
            Pokušaj ponovno
          </Button>
          <TextLink href="/countdown/">Natrag na odbrojavanje</TextLink>
        </div>
      </Envelope>
    );
  }

  if (state.kind === "not-drawn") {
    return (
      <Envelope action={<LogoutLink />}>
        <div className={styles.status}>
          <p>Izvlačenje još nije održano. Navrati malo kasnije.</p>
          <TextLink href="/countdown/">Natrag na odbrojavanje</TextLink>
        </div>
      </Envelope>
    );
  }

  const { vocativeName, reveal: result } = state;
  const message = revealMessage(vocativeName, result.receiver_accusative_name);
  return (
    <Envelope action={<LogoutLink />}>
      <div className={styles.layout}>
        <p className={styles.message}>{message}</p>
        <h1 className={styles.name}>{result.receiver_display_name}</h1>
        <div className={styles.media}>
          <RevealAnimation />
        </div>
        <TextLink href="/countdown/" className={styles.back}>
          Natrag na odbrojavanje
        </TextLink>
      </div>
    </Envelope>
  );
}
