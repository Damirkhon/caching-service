from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from httpx import ASGITransport, AsyncClient
from starlette.types import Message, Receive, Scope, Send

from caching_service.api.middleware import (
    MAX_ESCAPED_BYTES_PER_CHARACTER,
    MAX_REQUEST_BYTES,
    BodySizeLimitMiddleware,
)
from caching_service.api.schemas import MAX_ITEMS, MAX_STRING_LENGTH

LIMIT = 100


def client() -> AsyncClient:
    # A FastAPI app, so an oversized stream is answered the way the real service answers it.
    app = FastAPI()

    @app.post("/")
    async def echo_length(request: Request) -> PlainTextResponse:
        return PlainTextResponse(str(len(await request.body())))

    app.add_middleware(BodySizeLimitMiddleware, max_bytes=LIMIT)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


class RecordingApp:
    """An inner ASGI app that reads one message and records what it saw."""

    def __init__(self) -> None:
        self.messages: list[Message] = []

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.messages.append(await receive())


async def no_send(message: Message) -> None:
    raise AssertionError(message)


async def chunks(total: int, size: int = 10) -> AsyncIterator[bytes]:
    for start in range(0, total, size):
        yield b"x" * min(size, total - start)


@pytest.mark.parametrize("size", [0, LIMIT])
async def test_bodies_up_to_the_limit_pass(size: int) -> None:
    async with client() as c:
        response = await c.post("/", content=b"x" * size)

    assert (response.status_code, response.text) == (200, str(size))


async def test_declared_oversized_body_is_refused_before_reading() -> None:
    async with client() as c:
        response = await c.post("/", content=b"x" * (LIMIT + 1))

    assert response.status_code == 413
    assert response.json() == {"detail": "Request body too large."}


async def test_streamed_oversized_body_is_refused() -> None:
    # A generator body is sent chunked, with no Content-Length to check up front.
    async with client() as c:
        response = await c.post("/", content=chunks(LIMIT + 1))

    assert response.status_code == 413
    assert response.json() == {"detail": "Request body too large."}


async def test_streamed_body_within_the_limit_passes() -> None:
    async with client() as c:
        response = await c.post("/", content=chunks(LIMIT))

    assert (response.status_code, response.text) == (200, str(LIMIT))


async def test_malformed_content_length_is_left_to_the_server() -> None:
    async with client() as c:
        response = await c.post("/", content=b"x", headers={"content-length": "not-a-number"})

    assert response.status_code != 413


def test_limit_admits_the_largest_valid_request_fully_escaped() -> None:
    strings = 2 * MAX_ITEMS
    assert strings * MAX_STRING_LENGTH * MAX_ESCAPED_BYTES_PER_CHARACTER < MAX_REQUEST_BYTES


async def test_non_http_scopes_pass_through_untouched() -> None:
    inner = RecordingApp()
    startup: Message = {"type": "lifespan.startup"}

    async def receive() -> Message:
        return startup

    await BodySizeLimitMiddleware(inner, max_bytes=LIMIT)({"type": "lifespan"}, receive, no_send)

    assert inner.messages == [startup]


async def test_disconnect_messages_are_passed_on_without_counting() -> None:
    inner = RecordingApp()
    disconnect: Message = {"type": "http.disconnect"}

    async def receive() -> Message:
        return disconnect

    scope: Scope = {"type": "http", "headers": []}
    await BodySizeLimitMiddleware(inner, max_bytes=LIMIT)(scope, receive, no_send)

    assert inner.messages == [disconnect]
