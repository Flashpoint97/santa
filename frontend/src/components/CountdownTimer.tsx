"use client";

import { useEffect, useState } from "react";
import { plural, remainingUntil } from "@/lib/format";
import styles from "./CountdownTimer.module.css";
import PaperCard from "./ui/PaperCard";

type Props = {
  /** ISO-8601 exchange time. */
  exchangeAt: string;
  /** Shown instead of the countdown once the time has passed. */
  afterEventMessage: string;
};

const pad = (n: number) => String(n).padStart(2, "0");

export default function CountdownTimer({ exchangeAt, afterEventMessage }: Props) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const remaining = remainingUntil(new Date(exchangeAt).getTime(), now);
  if (!remaining) {
    return (
      <PaperCard variant="celebration" label="Sretno!" stamp className={styles.after}>
        <p>{afterEventMessage}</p>
      </PaperCard>
    );
  }

  const blocks = [
    { value: String(remaining.days), unit: plural(remaining.days, "dan", "dana", "dana") },
    { value: pad(remaining.hours), unit: plural(remaining.hours, "sat", "sata", "sati") },
    { value: pad(remaining.minutes), unit: "min" },
    { value: pad(remaining.seconds), unit: "s" },
  ];

  return (
    <div role="timer" aria-live="off">
      <span className="sr-only">
        Još {remaining.days} {plural(remaining.days, "dan", "dana", "dana")}, {remaining.hours}{" "}
        {plural(remaining.hours, "sat", "sata", "sati")}, {remaining.minutes} min i{" "}
        {remaining.seconds} s
      </span>
      <ol className={styles.blocks} aria-hidden="true">
        {blocks.map((b, i) => (
          <li key={i} className={styles.block}>
            <span className={styles.digits}>{b.value}</span>
            <span className={`label ${styles.unit}`}>{b.unit}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}
