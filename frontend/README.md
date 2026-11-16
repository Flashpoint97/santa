# Frontend

Next.js (App Router, TypeScript strict), static export. All data comes from the backend API.

```
cp .env.example .env.local   # then adjust NEXT_PUBLIC_API_URL
npm install
npm run dev                  # http://localhost:3000
npm run lint && npm run typecheck && npm run build   # build writes the static site to out/
```

- `NEXT_PUBLIC_API_URL`: backend base URL (public, no secrets).
- `BASE_PATH`: build-time sub-path for GitHub Pages (the repo name, e.g. `/santa`). Empty locally.

Layout: `src/lib/` (API client, session token, formatting, wake/health polling), `src/components/` (screens), `src/app/` (routes: `/` code screen, `/countdown/`).

Every entry page is wrapped in `<WakeGate>` (`src/components/WakeGate.tsx`): it polls `GET /health` (API root) with backoff for up to about 90 s and shows the "waking up" screen until it answers 200. Wrap new entry pages the same way.
