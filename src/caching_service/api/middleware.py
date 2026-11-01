"""Rejects oversized request bodies before the framework buffers and parses them."""

from typing import Final

from fastapi import HTTPException, status
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from caching_service.api.schemas import MAX_ITEMS, MAX_STRING_LENGTH

# A JSON encoder may escape every character; an astral one becomes a surrogate pair,
# "\uXXXX\uXXXX", which is 12 bytes. Python's json.dumps escapes non-ASCII by default.
MAX_ESCAPED_BYTES_PER_CHARACTER: Final = 12
# Room for everything around the strings: keys, quotes, commas, brackets and whitespace.
JSON_SYNTAX_ALLOWANCE_BYTES: Final = 1024 * 1024
# The largest valid request, however it is encoded; anything bigger can only fail validation,
# so it is refused before being read into memory.
MAX_REQUEST_BYTES: Final = (
    2 * MAX_ITEMS * MAX_STRING_LENGTH * MAX_ESCAPED_BYTES_PER_CHARACTER
    + JSON_SYNTAX_ALLOWANCE_BYTES
)
REQUEST_TOO_LARGE: Final = "Request body too large."


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self._app = app
        self._max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        declared = _declared_length(scope)
        if declared is not None and declared > self._max_bytes:
            response = JSONResponse(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                content={"detail": REQUEST_TOO_LARGE},
            )
            await response(scope, receive, send)
            return

        # A body sent without a length (chunked) is counted as it arrives instead.
        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self._max_bytes:
                    # Raised while the route reads its body, so the framework's own exception
                    # handling turns it into the response.
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=REQUEST_TOO_LARGE
                    )
            return message

        await self._app(scope, limited_receive, send)


def _declared_length(scope: Scope) -> int | None:
    for name, value in scope["headers"]:
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                # Malformed headers are the server's to reject, not this middleware's.
                return None
    return None
