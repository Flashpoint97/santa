import pytest
from pydantic import ValidationError

from app.config import Settings, normalize_database_url

GOOD_SECRET = "abcdefghijklmnopqrstuvwxyz0123456789"  # 36 chars, 36 distinct
PROD = {
    "environment": "production",
    "code_hmac_secret": GOOD_SECRET,
    "database_url": "postgresql://u:p@db.example.test/santa",
    "cors_origins": ["https://pages.example.test"],
}


def make(**overrides: object) -> Settings:
    # _env_file=None and explicit kwargs keep a developer's real .env out of the test.
    return Settings(_env_file=None, **{**PROD, **overrides})  # type: ignore[arg-type]


def test_defaults_match_the_design() -> None:
    s = Settings(_env_file=None, code_hmac_secret=GOOD_SECRET)  # type: ignore[call-arg]
    assert s.session_idle_timeout_minutes == 10
    assert (s.login_failures_per_ip, s.login_failures_per_ip_window_minutes) == (10, 15)
    assert (s.login_failures_global, s.login_failures_global_window_minutes) == (50, 60)
    assert s.cors_origins == ["http://localhost:3000"]


def test_production_config_accepted() -> None:
    assert make().environment == "production"


def test_secret_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CODE_HMAC_SECRET")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_secret_must_be_long() -> None:
    with pytest.raises(ValidationError):
        make(code_hmac_secret="short")


@pytest.mark.parametrize(
    "override",
    [
        {"cors_origins": ["http://localhost:3000"]},
        {"cors_origins": ["*"]},
        {"cors_origins": []},
        {"code_hmac_secret": "a" * 40},
        {"database_url": "postgresql://u:p@localhost/santa"},
    ],
)
def test_production_guards_fail_closed(override: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        make(**override)


def test_database_url_is_normalised_for_asyncpg() -> None:
    url = normalize_database_url("postgresql://u:p@h/db?sslmode=require&channel_binding=require")
    assert url == "postgresql+asyncpg://u:p@h/db?ssl=require"
