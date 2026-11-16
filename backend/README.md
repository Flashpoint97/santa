# Secret Santa backend

FastAPI + async SQLAlchemy + Postgres. Admin work is done from the terminal (`python -m app.cli`);
there is no admin API. Run everything from this directory with `uv`.

## Local setup

```
cp .env.example .env            # then edit: CODE_HMAC_SECRET (>= 32 chars), DATABASE_URL
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --port 8000          # CORS allows http://localhost:3000 by default
```

## List file (organizer's private input)

JSON. The real file lives only in the gitignored `private/` folder at the repo root and is never
committed; `list.example.json` is a placeholder with obviously fake data.

```
{
  "event": {
    "name": "string",
    "budget_amount": "20.00",        // decimal, up to 2 places
    "budget_currency": "EUR",        // 3 capital letters, default EUR
    "exchange_at": "2026-12-25T12:00", // Europe/Zagreb wall-clock time, NO offset or Z; stored as UTC
    "place": "string",
    "after_event_message": "string"  // shown once exchange_at has passed
  },
  "participants": [                  // at least 2
    {
      "display_name": "string",      // unique (case-insensitive)
      "vocative_name": "string",     // used in greetings
      "accusative_name": "string",   // used in "you give to ..."
      "partner": "other display_name or null", // must point back at this person
      "note": "personal note (optional)",
      "gif": "file.gif (optional, relative to the list file; converted in a later step)"
    }
  ]
}
```
(The `//` comments are explanatory only: real JSON has no comments.)

`load` checks everything first (unique names, partner links valid and symmetric, a real
Zagreb local time) and writes nothing if anything is wrong. It refuses if an event already exists.

## Dev seed

```
uv run python -m app.cli load list.example.json
```

prints one 6-digit Code per participant, once (only their HMAC is stored). To start over, drop and
recreate the dev database, then `alembic upgrade head`.

## Draw

```
uv run python -m app.cli draw             # prints only "Draw complete: N participants."
uv run python -m app.cli draw --redraw    # replaces an existing draw atomically
```

Exclusions are the Partner links (both directions). A second `draw` is refused without `--redraw`;
an impossible draw writes nothing and exits 1. A re-draw also resets everyone's first-reveal flag.
Assignments are never printed or logged.

## Organizer commands

```
uv run python -m app.cli new-code "Osoba B"   # lost Code: prints a new one, once
uv run python -m app.cli status               # "Osoba A: da" / "Osoba A: ne" (logged in), sorted
uv run python -m app.cli wipe                 # after the event: delete everything
```

- `new-code <name>`: the name is matched case-insensitively; an unknown name exits 1 and changes
  nothing. In one transaction it stores a new unique Code's HMAC and deletes all of that
  Participant's Sessions, so the old Code and the old tokens stop working at once. The Code is
  printed once on stdout and never logged. The Participant's logged-in flag is left as it was.
- `status`: one line per Participant, name plus `da`/`ne` (has logged in). Nothing about
  Assignments, Reveals, Codes or times. Exits 1 if no event is loaded.
- `wipe`: asks you to type `OBRIŠI` (read from stdin; anything else, or no input, deletes nothing
  and exits 1). It then deletes the event, which cascades to Participants, Sessions, Assignments
  and Animations, and also all rate-limit rows, so no table keeps any data. There is no
  `--yes` flag on purpose; scripts can pipe the word in: `echo OBRIŠI | python -m app.cli wipe`.

Logs go to stderr and never contain names, Codes or Assignments (participant ids at most).

## Sessions and login limits

- A Session is valid while now - `last_activity_at` <= `SESSION_IDLE_TIMEOUT_MINUTES` (10). Every
  authenticated request and `POST /api/v1/session/heartbeat` (204) bump it in one atomic UPDATE;
  an expired Session is deleted and answered with 401. `DELETE /api/v1/session` (204) logs out.
- Failed Codes are limited per client IP (`LOGIN_FAILURES_PER_IP` per `..._WINDOW_MINUTES`) and
  site-wide (`LOGIN_FAILURES_GLOBAL`), answered with 429; only failures are recorded and a
  locked-out request never checks the Code. Check + record is atomic: on Postgres a
  transaction-scoped advisory lock per key (IP key and site-wide key, taken in sorted order) is
  held from the count until the commit, so concurrent guesses cannot exceed a limit (SQLite takes
  its database write lock instead). Consequence: login attempts are serialised site-wide, fine
  at this size. See `app/services/rate_limit.py`.

## Checks

```
uv run ruff check . && uv run ruff format --check . && uv run ty check app
uv run pytest                                   # SQLite fallback
TEST_DATABASE_URL=postgresql+asyncpg://... uv run pytest   # Postgres
```
