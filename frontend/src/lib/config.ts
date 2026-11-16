// Inlined at build time. Public by design: never put secrets in NEXT_PUBLIC_* vars.
export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "").replace(/\/+$/, "");
