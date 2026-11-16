"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, getMe, reveal, type Reveal } from "@/lib/api";
import { firstTimeMessage, reminderMessage } from "@/lib/revealMessages";
import { clearToken, getToken } from "@/lib/session";
import { useIdleSession } from "@/lib/useIdleSession";

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
  // Revealing has a side effect (it spends the one-time "first time"), so the request is made
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
      <main>
        <p>Učitavam…</p>
      </main>
    );
  }

  if (state.kind === "error") {
    return (
      <main>
        <p role="alert">Ne mogu otvoriti poklon. Pokušaj ponovno.</p>
        <button
          type="button"
          onClick={() => {
            setState({ kind: "loading" });
            setAttempt((n) => n + 1);
          }}
        >
          Pokušaj ponovno
        </button>
        <p>
          <Link href="/countdown/">Natrag na odbrojavanje</Link>
        </p>
      </main>
    );
  }

  if (state.kind === "not-drawn") {
    return (
      <main>
        <p>Izvlačenje još nije održano. Navrati malo kasnije.</p>
        <p>
          <Link href="/countdown/">Natrag na odbrojavanje</Link>
        </p>
      </main>
    );
  }

  const { vocativeName, reveal: result } = state;
  const message = result.first_time
    ? firstTimeMessage(vocativeName, result.receiver_accusative_name)
    : reminderMessage(vocativeName, result.receiver_accusative_name);
  return (
    <main>
      <p>{message}</p>
      <h1>{result.receiver_display_name}</h1>

      {/* TICKET 05: the looping Animation of the receiver goes here (a plain <video>, fetched
          with the bearer token from GET /me/receiver/animation). Nothing is built yet. */}
      <div data-slot="reveal-animation" />

      <p>
        <Link href="/countdown/">Natrag na odbrojavanje</Link>
      </p>
    </main>
  );
}
