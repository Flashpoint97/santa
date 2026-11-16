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

    # --- auth (no defaults for secrets: the app refuses to start without them) ---
    jwt_secret: SecretStr = Field(min_length=32)
    code_hmac_secret: SecretStr = Field(min_length=32)
    jwt_ttl_minutes: int = Field(default=120, ge=5, le=24 * 60)
    login_code_ttl_minutes: int = Field(default=10, ge=1, le=30)
    login_code_max_attempts: int = Field(default=5, ge=1, le=10)

    # --- rate limits (sliding window) ---
    rate_limit_window_minutes: int = Field(default=60, ge=1)
    code_requests_per_email: int = Field(default=5, ge=1)
    code_requests_per_ip: int = Field(default=20, ge=1)
    code_verifications_per_ip: int = Field(default=30, ge=1)

    # --- CORS: the GitHub Pages origin (scheme + host, no path) and localhost in dev ---
    cors_origins: list[str] = ["http://localhost:3000"]

    # --- email ---
    email_backend: Literal["console", "postmark"] = "console"
    postmark_server_token: SecretStr | None = None
    email_from: str = "Secret Santa <santa@example.com>"
    frontend_url: str = "http://localhost:3000"

    # --- observability ---
    metrics_enabled: bool = False

    @field_validator("database_url")
    @classmethod
    def _normalize_db_url(cls, v: str) -> str:
        return normalize_database_url(v)

    @model_validator(mode="after")
    def _production_guards(self) -> "Settings":
        # Fail closed: the console backend would print login codes to the host's logs.
        if self.environment == "production":
            if self.email_backend != "postmark":
                raise ValueError("production requires EMAIL_BACKEND=postmark")
            if self.postmark_server_token is None:
                raise ValueError("production requires POSTMARK_SERVER_TOKEN")
            if any("localhost" in o for o in self.cors_origins):
                raise ValueError("production CORS_ORIGINS must not include localhost")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # secrets come from the environment


settings = get_settings()
