"""Runtime configuration for the service, read from ``CACHE_SERVICE_*`` environment variables."""

from typing import Final, Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_PREFIX: Final = "CACHE_SERVICE_"

# Well above any sensible fan-out; it exists so a typo cannot open thousands of connections
# to the external transformer at once.
MAX_TRANSFORMER_CONCURRENCY: Final = 1000

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class Settings(BaseSettings):
    """Validated service settings.

    Built once at startup and passed to ``create_app()`` explicitly instead of living in a
    module-level global, so tests construct their own instance rather than patching one.
    Frozen because configuration must not change while the service runs.
    """

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_file=".env",
        extra="ignore",
        frozen=True,
    )

    # A plain string: pydantic's DSN types reject relative SQLite paths, and SQLAlchemy
    # validates the URL when the engine is created anyway. Hidden from repr because it may
    # carry a password and settings objects end up in logs and tracebacks.
    database_url: str = Field(default="sqlite+aiosqlite:///./data/cache.db", repr=False)
    # Infinity is rejected: it would make the simulated transformer hang forever or
    # silently disable the timeout.
    transformer_latency_seconds: float = Field(default=0.5, ge=0, allow_inf_nan=False)
    transformer_timeout_seconds: float = Field(default=5.0, gt=0, allow_inf_nan=False)
    transformer_max_concurrency: int = Field(default=10, ge=1, le=MAX_TRANSFORMER_CONCURRENCY)
    log_level: LogLevel = "INFO"
