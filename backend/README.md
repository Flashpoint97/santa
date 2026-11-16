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

## Checks

```
uv run ruff check . && uv run ruff format --check . && uv run ty check app
uv run pytest                                   # SQLite fallback
TEST_DATABASE_URL=postgresql+asyncpg://... uv run pytest   # Postgres
```
