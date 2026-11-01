from typing import Any

from caching_service.config import Settings


# Any: overrides are forwarded verbatim to Settings, which validates every field itself.
def make_settings(**overrides: Any) -> Settings:
    """Build settings for tests without reading a developer's local ``.env`` file."""
    return Settings(_env_file=None, **overrides)
