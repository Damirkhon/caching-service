from pathlib import Path

import pytest
from pydantic import ValidationError

from caching_service.config import ENV_PREFIX, Settings
from tests.factories import make_settings


def test_defaults() -> None:
    settings = make_settings()

    assert settings.database_url == "sqlite+aiosqlite:///./data/cache.db"
    assert settings.transformer_latency_seconds == pytest.approx(0.5)
    assert settings.transformer_timeout_seconds == pytest.approx(5.0)
    assert settings.transformer_max_concurrency == 10
    assert settings.log_level == "INFO"


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CACHE_SERVICE_DATABASE_URL", "postgresql+asyncpg://u:p@db/cache")
    monkeypatch.setenv("CACHE_SERVICE_TRANSFORMER_LATENCY_SECONDS", "0")
    monkeypatch.setenv("CACHE_SERVICE_TRANSFORMER_TIMEOUT_SECONDS", "1.5")
    monkeypatch.setenv("CACHE_SERVICE_TRANSFORMER_MAX_CONCURRENCY", "3")
    monkeypatch.setenv("CACHE_SERVICE_LOG_LEVEL", "DEBUG")

    settings = make_settings()

    assert settings.database_url == "postgresql+asyncpg://u:p@db/cache"
    assert settings.transformer_latency_seconds == 0
    assert settings.transformer_timeout_seconds == pytest.approx(1.5)
    assert settings.transformer_max_concurrency == 3
    assert settings.log_level == "DEBUG"


def test_env_file_is_read(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("CACHE_SERVICE_LOG_LEVEL=WARNING\n")

    settings = Settings(_env_file=env_file)

    assert settings.log_level == "WARNING"


def test_unprefixed_env_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@elsewhere/other")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    settings = make_settings()

    assert settings.database_url == "sqlite+aiosqlite:///./data/cache.db"
    assert settings.log_level == "INFO"


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("TRANSFORMER_LATENCY_SECONDS", "-1"),
        ("TRANSFORMER_LATENCY_SECONDS", "inf"),
        ("TRANSFORMER_TIMEOUT_SECONDS", "0"),
        ("TRANSFORMER_TIMEOUT_SECONDS", "inf"),
        ("TRANSFORMER_MAX_CONCURRENCY", "0"),
        ("TRANSFORMER_MAX_CONCURRENCY", "1001"),
        ("LOG_LEVEL", "LOUD"),
    ],
)
def test_invalid_values_rejected(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str
) -> None:
    monkeypatch.setenv(f"{ENV_PREFIX}{variable}", value)

    with pytest.raises(ValidationError, match=variable.lower()):
        make_settings()


def test_settings_are_immutable() -> None:
    settings = make_settings()

    with pytest.raises(ValidationError, match="frozen"):
        settings.log_level = "DEBUG"  # type: ignore[misc]  # the assignment must fail at runtime


def test_database_password_is_not_in_repr() -> None:
    settings = make_settings(database_url="postgresql+asyncpg://user:s3cret@db/cache")

    assert "s3cret" not in repr(settings)
