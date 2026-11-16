// Display helpers. Everything is shown in Croatian and in Europe/Zagreb time.
const ZAGREB = "Europe/Zagreb";

export function formatExchangeTime(iso: string): string {
  return new Intl.DateTimeFormat("hr-HR", {
    timeZone: ZAGREB,
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(iso));
}

/** "50 €" for whole amounts, "12,50 €" otherwise. Falls back to plain text. */
export function formatBudget(amount: string, currency: string): string {
  const value = Number(amount);
  if (!Number.isFinite(value)) return `${amount} ${currency}`;
  try {
    return new Intl.NumberFormat("hr-HR", {
      style: "currency",
      currency,
      minimumFractionDigits: Number.isInteger(value) ? 0 : 2,
      maximumFractionDigits: 2,
    }).format(value);
  } catch {
    return `${amount} ${currency}`;
  }
}

export type Remaining = {
  days: number;
  hours: number;
  minutes: number;
  seconds: number;
};

/** Time left until `targetMs`, or null once it has passed. */
export function remainingUntil(targetMs: number, nowMs: number): Remaining | null {
  const diff = Math.floor((targetMs - nowMs) / 1000);
  if (diff <= 0) return null;
  return {
    days: Math.floor(diff / 86400),
    hours: Math.floor((diff % 86400) / 3600),
    minutes: Math.floor((diff % 3600) / 60),
    seconds: diff % 60,
  };
}

/** Croatian plural: one / few (2-4) / many. */
export function plural(n: number, one: string, few: string, many: string): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}
