import logging

import pytest
from fastapi import FastAPI

from caching_service import __version__
from caching_service.main import configure_logging, create_app
from caching_service.services.payloads import PayloadService
from tests.factories import make_settings
from tests.fakes import CountingTransformer

pytestmark = pytest.mark.integration


async def test_lifespan_wires_one_payload_service(database_url: str) -> None:
    app = create_app(make_settings(database_url=database_url), transformer=CountingTransformer())

    async with app.router.lifespan_context(app):
        assert isinstance(app.state.payload_service, PayloadService)


async def test_lifespan_disposes_the_engine_on_shutdown(database_url: str) -> None:
    app = create_app(make_settings(database_url=database_url), transformer=CountingTransformer())

    async with app.router.lifespan_context(app):
        engine = app.state.engine
        async with engine.connect():
            pass
        assert engine.pool.checkedin() == 1

    # Disposal closes pooled connections; a disposed engine starts over with an empty pool.
    assert engine.pool.checkedin() == 0


def test_app_metadata(database_url: str) -> None:
    app: FastAPI = create_app(make_settings(database_url=database_url))

    assert (app.title, app.version) == ("Caching Service", __version__)


def test_configure_logging_sets_the_package_level_only() -> None:
    root_level = logging.getLogger().level

    configure_logging("DEBUG")

    assert logging.getLogger("caching_service").level == logging.DEBUG
    assert logging.getLogger().level == root_level
