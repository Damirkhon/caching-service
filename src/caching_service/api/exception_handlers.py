"""The one place that maps domain errors to HTTP responses."""

import logging
from typing import Final, cast

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from caching_service.errors import PayloadNotFoundError, TransformerError

logger = logging.getLogger(__name__)

PAYLOAD_NOT_FOUND: Final = "Payload not found."
TRANSFORMER_FAILED: Final = "Transformer service failed; please retry."
# Error locations can contain client-chosen keys (an unknown field's name); they are cut to
# this length so a request can't make the error response echo arbitrary amounts of its data.
MAX_ECHOED_LOCATION_LENGTH: Final = 64


def register_exception_handlers(app: FastAPI) -> None:
    # Anything not mapped here is a bug: Starlette answers 500 and the traceback is logged,
    # never returned to the client.
    app.add_exception_handler(RequestValidationError, _validation_failed)
    app.add_exception_handler(PayloadNotFoundError, _payload_not_found)
    app.add_exception_handler(TransformerError, _transformer_failed)


async def _validation_failed(_request: Request, exc: Exception) -> JSONResponse:
    # FastAPI's format minus the rejected input, which it would otherwise echo back: that is
    # user data, can be a megabyte of strings, and may not even be encodable as JSON.
    # The cast is safe: this handler is registered for RequestValidationError only.
    validation_error = cast(RequestValidationError, exc)
    errors = [
        {
            **{key: value for key, value in error.items() if key not in {"input", "loc"}},
            "loc": [_truncated(part) for part in error["loc"]],
        }
        for error in validation_error.errors()
    ]
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"detail": jsonable_encoder(errors)},
    )


async def _payload_not_found(_request: Request, _exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND, content={"detail": PAYLOAD_NOT_FOUND}
    )


async def _transformer_failed(request: Request, exc: Exception) -> JSONResponse:
    # An upstream failure rather than a bug: a warning with the cause chain, and a 502 telling
    # the client a retry may succeed (successful transformations were already cached).
    logger.warning("transformer failure on %s %s", request.method, request.url.path, exc_info=exc)
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY, content={"detail": TRANSFORMER_FAILED}
    )


def _truncated(part: int | str) -> int | str:
    if isinstance(part, str):
        return part[:MAX_ECHOED_LOCATION_LENGTH]
    return part
