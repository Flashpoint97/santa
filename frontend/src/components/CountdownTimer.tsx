"use client";

import { useEffect, useState } from "react";
import { plural, remainingUntil } from "@/lib/format";

type Props = {
  /** ISO-8601 exchange time. */
  exchangeAt: string;
  /** Shown instead of the countdown once the time has passed. */
  afterEventMessage: string;
};

export default function CountdownTimer({ exchangeAt, afterEventMessage }: Props) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const remaining = remainingUntil(new Date(exchangeAt).getTime(), now);
  if (!remaining) {
    return <p>{afterEventMessage}</p>;
  }

  return (
    <p role="timer" aria-live="off">
      Još {remaining.days} {plural(remaining.days, "dan", "dana", "dana")},{" "}
      {remaining.hours} {plural(remaining.hours, "sat", "sata", "sati")},{" "}
      {remaining.minutes} min i {remaining.seconds} s
    </p>
  );
}
