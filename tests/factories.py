from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from httpx import ASGITransport, AsyncClient

from caching_service.config import Settings
from caching_service.domain.transformer import Transformer
from caching_service.main import create_app


# Any: overrides are forwarded verbatim to Settings, which validates every field itself.
def make_settings(**overrides: Any) -> Settings:
    """Build settings for tests without reading a developer's local ``.env`` file."""
    return Settings(_env_file=None, **overrides)


@asynccontextmanager
async def api_client(
    database_url: str,
    transformer: Transformer,
    *,
    raise_app_exceptions: bool = True,
    root_path: str = "",
) -> AsyncIterator[AsyncClient]:
    """An in-process client for a fresh app, run through its real lifespan."""
    app = create_app(make_settings(database_url=database_url), transformer=transformer)
    transport = ASGITransport(
        app=app, raise_app_exceptions=raise_app_exceptions, root_path=root_path
    )
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        yield client
