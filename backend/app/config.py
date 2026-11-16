"""Typed, env-driven configuration. Import the single `settings` instance; never call os.getenv."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


def normalize_database_url(raw: str) -> str:
    """Accept the URL as Neon/Render hand it out and make it usable by asyncpg.

    Neon gives `postgresql://...?sslmode=require&channel_binding=require`; asyncpg does not
    understand libpq's `sslmode`/`channel_binding` keys, so translate them to `ssl=require`.
    """
    url = make_url(raw)
    if url.drivername in ("postgres", "postgresql", "postgresql+psycopg2"):
        url = url.set(drivername="postgresql+asyncpg")
    if url.drivername == "postgresql+asyncpg":
        query = dict(url.query)
        sslmode = query.pop("sslmode", None)
        query.pop("channel_binding", None)
        if sslmode and sslmode != "disable" and "ssl" not in query:
            query["ssl"] = "require"
        url = url.set(query=query)
    return url.render_as_string(hide_password=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- runtime ---
    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"

    # --- database ---
    database_url: str = "postgresql+asyncpg://santa:santa@localhost:5432/santa"
    db_statement_timeout_ms: int = Field(default=5000, ge=100)
    db_health_timeout_s: float = Field(default=2.0, gt=0)

    # --- codes and sessions (no default for the secret: the app refuses to start without it) ---
    # Keys the HMAC of login codes and of rate-limit keys. Rotating it invalidates every code.
    code_hmac_secret: SecretStr = Field(min_length=32)
    session_idle_timeout_minutes: int = Field(default=10, ge=1, le=24 * 60)

    # --- brute-force limits on *failed* logins (sliding windows) ---
    login_failures_per_ip: int = Field(default=10, ge=1)
    login_failures_per_ip_window_minutes: int = Field(default=15, ge=1)
    login_failures_global: int = Field(default=50, ge=1)
    login_failures_global_window_minutes: int = Field(default=60, ge=1)

    # --- CORS: the GitHub Pages origin (scheme + host, no path) and localhost in dev ---
    cors_origins: list[str] = ["http://localhost:3000"]

    @field_validator("database_url")
    @classmethod
    def _normalize_db_url(cls, v: str) -> str:
        return normalize_database_url(v)

    @model_validator(mode="after")
    def _production_guards(self) -> "Settings":
        # Fail closed: refuse to boot in production with development leftovers.
        if self.environment == "production":
            if not self.cors_origins or any(
                o == "*" or "localhost" in o or "127.0.0.1" in o for o in self.cors_origins
            ):
                raise ValueError(
                    "production CORS_ORIGINS must list the real frontend origin only "
                    "(no '*', no localhost)"
                )
            if len(set(self.code_hmac_secret.get_secret_value())) < 10:
                raise ValueError("production CODE_HMAC_SECRET looks like a placeholder")
            host = make_url(self.database_url).host
            if host in (None, "", "localhost", "127.0.0.1", "::1"):
                raise ValueError("production DATABASE_URL must point at the real database")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # secrets come from the environment


settings = get_settings()
