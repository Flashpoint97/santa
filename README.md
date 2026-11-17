# Tajni Djed Mraz

**🎁 Stranica / Website: https://flashpoint97.github.io/santa/**

Upiši svoj 6-znamenkasti kod da vidiš odbrojavanje i koga daruješ.
Enter your 6-digit code to see the countdown and who you're giving a gift to.

> The first visit after a quiet period can take up to a minute ("Budimo patuljke…") while the
> free server wakes up.

## How it works

- **Website:** static Next.js app on GitHub Pages (`frontend/`).
- **API:** FastAPI on Render (`backend/`), using a Postgres database on Neon.
- Each participant logs in with a personal code from the organizer. They see only their own page.
  Nobody else can see who drew whom, the organizer included.

Developer notes: [backend/README.md](backend/README.md), [frontend/README.md](frontend/README.md).
