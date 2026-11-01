"""Application factory: one FastAPI app with its services wired for the life of the process."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

from fastapi import FastAPI

from caching_service import __version__
from caching_service.api.exception_handlers import register_exception_handlers
from caching_service.api.routes import router
from caching_service.config import LogLevel, Settings
from caching_service.db.session import create_engine, create_session_factory
from caching_service.domain.transformer import SimulatedTransformer, Transformer
from caching_service.services.health import HealthService
from caching_service.services.payloads import PayloadService
from caching_service.services.transformation_cache import TransformationCache

LOG_FORMAT: Final = "%(asctime)s %(levelname)s %(name)s %(message)s"


def create_app(
    settings: Settings | None = None, *, transformer: Transformer | None = None
) -> FastAPI:
    """Build the app; tests pass their own settings and a fake transformer.

    A factory rather than a module-level app, so configuration is read when the app is built,
    not at import, and every test gets an isolated app without patching globals.
    """
    resolved = Settings() if settings is None else settings

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(resolved.log_level)
        # The schema is not created here: it belongs to the migrations.
        engine = create_engine(resolved)
        try:
            session_factory = create_session_factory(engine)
            # One cache per process: single-flight and the concurrency limit only work if every
            # request shares them.
            cache = TransformationCache(
                session_factory,
                transformer or SimulatedTransformer(resolved.transformer_latency_seconds),
                timeout_seconds=resolved.transformer_timeout_seconds,
                max_concurrency=resolved.transformer_max_concurrency,
            )
            app.state.payload_service = PayloadService(cache, session_factory)
            app.state.health_service = HealthService(engine)
            # Not used by the routes; kept so shutdown behaviour can be observed.
            app.state.engine = engine
            yield
        finally:
            await engine.dispose()

    app = FastAPI(title="Caching Service", version=__version__, lifespan=lifespan)
    app.include_router(router)
    register_exception_handlers(app)
    return app


def configure_logging(level: LogLevel) -> None:
    # Adds a handler only if none exists yet (uvicorn or a test runner may have set one up),
    # and sets the level on this package only, leaving third-party loggers as configured.
    logging.basicConfig(format=LOG_FORMAT)
    logging.getLogger("caching_service").setLevel(level)
