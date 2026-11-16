"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { ApiError, login, NetworkError } from "@/lib/api";
import { setToken } from "@/lib/session";
import styles from "./CodeScreen.module.css";
import Button from "./ui/Button";
import Envelope from "./ui/Envelope";
import PaperCard from "./ui/PaperCard";

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
    <Envelope>
      <div className={styles.layout}>
        <div>
          <h1>
            Posebna
            <br />
            dostava.
          </h1>
          <p className={styles.intro}>Upiši svoj kod da vidiš svoju stranicu.</p>
          <form onSubmit={onSubmit} className={styles.form}>
            <label htmlFor="code" className={`label ${styles.fieldLabel}`}>
              Tvoj kod ({CODE_LENGTH} znamenki)
            </label>
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
              aria-invalid={error ? true : undefined}
              aria-describedby={error ? "code-error" : undefined}
              className={styles.input}
            />
            {error && (
              <p role="alert" id="code-error" className={styles.error}>
                {error}
              </p>
            )}
            <Button type="submit" disabled={code.length !== CODE_LENGTH || submitting}>
              {submitting ? "Provjeravam…" : "Dalje"}
            </Button>
          </form>
        </div>
        <PaperCard label="Pošta sa Sjevernog pola" stamp className={styles.decor}>
          <h2>Samo za tvoje oči</h2>
          <p>Tvoj kod otvara samo tvoju stranicu.</p>
        </PaperCard>
      </div>
    </Envelope>
  );
}
