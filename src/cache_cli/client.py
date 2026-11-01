"""A small typed client for the caching service's payload endpoints."""

import uuid
from typing import Any, Final

import httpx
from pydantic import BaseModel, ValidationError

from cache_cli.errors import ServiceError, ServiceTimeoutError, ServiceUnavailableError
from cache_cli.models import CreatedPayload, PayloadOutput, PayloadRequest

PAYLOAD_PATH: Final = "/payload"
# Enough of an unexpected response body to identify it, without flooding the terminal.
MAX_DETAIL_LENGTH: Final = 200


class PayloadClient:
    """Wraps an httpx.Client whose base_url points at the service."""

    def __init__(self, http: httpx.Client) -> None:
        self._http = http

    def create(self, request: PayloadRequest) -> tuple[CreatedPayload, bool]:
        """Return the payload and whether it was newly created (201) rather than reused (200)."""
        response = self._send("POST", PAYLOAD_PATH, json=request.model_dump())
        if response.status_code not in (httpx.codes.CREATED, httpx.codes.OK):
            raise ServiceError(response.status_code, _detail(response))
        return _parse(CreatedPayload, response), response.status_code == httpx.codes.CREATED

    def get_output(self, payload_id: uuid.UUID) -> str:
        response = self._send("GET", f"{PAYLOAD_PATH}/{payload_id}")
        if response.status_code != httpx.codes.OK:
            raise ServiceError(response.status_code, _detail(response))
        return _parse(PayloadOutput, response).output

    # Any: forwarded verbatim to httpx.Client.request, whose keyword arguments vary by call.
    def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            return self._http.request(method, path, **kwargs)
        # Checked first: a timeout is a transport error too, but means a slow service, not an
        # unreachable one.
        except httpx.TimeoutException as exc:
            raise ServiceTimeoutError(str(self._http.base_url)) from exc
        except httpx.TransportError as exc:
            raise ServiceUnavailableError(str(self._http.base_url)) from exc


def _parse[M: BaseModel](model: type[M], response: httpx.Response) -> M:
    try:
        return model.model_validate_json(response.content)
    except ValidationError:
        raise ServiceError(response.status_code, "unexpected response body") from None


def _detail(response: httpx.Response) -> str:
    """The service's own error message when it sent one, otherwise the start of the body."""
    try:
        body = response.json()
    except ValueError:
        body = None
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):
        # FastAPI validation errors: one "location: message" per problem.
        return "; ".join(
            f"{'.'.join(str(p) for p in error.get('loc', [])[1:])}: {error.get('msg', '')}"
            for error in detail
            if isinstance(error, dict)
        )
    return response.text[:MAX_DETAIL_LENGTH] or response.reason_phrase
