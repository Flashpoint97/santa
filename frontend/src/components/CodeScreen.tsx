"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { ApiError, login, NetworkError } from "@/lib/api";
import { setToken } from "@/lib/session";

const CODE_LENGTH = 6;

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 429) return "Previše pokušaja. Pokušaj ponovno kasnije.";
    if (error.status === 400) return "Kod nije ispravan. Provjeri ga i pokušaj ponovno.";
    if (error.status === 422) return `Kod mora imati točno ${CODE_LENGTH} znamenki.`;
  }
  if (error instanceof NetworkError) {
    return "Ne mogu se spojiti na poslužitelj. Pokušaj ponovno.";
  }
  return "Nešto je pošlo po zlu. Pokušaj ponovno.";
}

export default function CodeScreen() {
  const router = useRouter();
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (code.length !== CODE_LENGTH || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const session = await login(code);
      setToken(session.token);
      router.push("/countdown/");
    } catch (e) {
      setError(errorMessage(e));
      setSubmitting(false);
    }
  }

  return (
    <main>
      <h1>Tajni Djed Mraz</h1>
      <p>Upiši svoj kod da vidiš svoju stranicu.</p>
      <form onSubmit={onSubmit}>
        <p>
          <label htmlFor="code">Tvoj kod ({CODE_LENGTH} znamenki)</label>
          <br />
          <input
            id="code"
            name="code"
            type="text"
            inputMode="numeric"
            pattern="[0-9]*"
            autoComplete="one-time-code"
            maxLength={CODE_LENGTH}
            value={code}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, CODE_LENGTH))}
            autoFocus
            required
          />
        </p>
        {error && <p role="alert">{error}</p>}
        <button type="submit" disabled={code.length !== CODE_LENGTH || submitting}>
          {submitting ? "Provjeravam…" : "Dalje"}
        </button>
      </form>
    </main>
  );
}
