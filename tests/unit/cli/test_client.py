import uuid
from collections.abc import Callable

import httpx
import pytest

from cache_cli.client import MAX_DETAIL_LENGTH, PayloadClient
from cache_cli.errors import (
    EXIT_FAILURE,
    EXIT_USAGE,
    ServiceError,
    ServiceTimeoutError,
    ServiceUnavailableError,
)
from cache_cli.models import PayloadRequest

REQUEST = PayloadRequest(list_1=["a"], list_2=["b"])
PAYLOAD_ID = uuid.uuid4()
BASE_URL = "http://service.test"


def client_for(handler: Callable[[httpx.Request], httpx.Response]) -> PayloadClient:
    return PayloadClient(httpx.Client(transport=httpx.MockTransport(handler), base_url=BASE_URL))


@pytest.mark.parametrize(("status", "created"), [(201, True), (200, False)])
def test_create_distinguishes_201_and_200(status: int, created: bool) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert (request.method, request.url.path) == ("POST", "/payload")
        return httpx.Response(status, json={"id": str(PAYLOAD_ID), "message": "ok"})

    payload, was_created = client_for(handler).create(REQUEST)

    assert (payload.id, was_created) == (PAYLOAD_ID, created)


def test_get_output_returns_the_output() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/payload/{PAYLOAD_ID}"
        return httpx.Response(200, json={"output": "A, B"})

    assert client_for(handler).get_output(PAYLOAD_ID) == "A, B"


@pytest.mark.parametrize(
    ("response", "message", "exit_code"),
    [
        (
            httpx.Response(502, json={"detail": "Transformer failed."}),
            "502: Transformer failed.",
            EXIT_FAILURE,
        ),
        (
            httpx.Response(
                422, json={"detail": [{"loc": ["body", "list_1", 0], "msg": "String too long"}]}
            ),
            "422: list_1.0: String too long",
            EXIT_USAGE,
        ),
        (
            httpx.Response(500, text="Internal Server Error"),
            "500: Internal Server Error",
            EXIT_FAILURE,
        ),
        (httpx.Response(503), "503: Service Unavailable", EXIT_FAILURE),
    ],
    ids=["detail-string", "validation-errors", "plain-text", "empty-body"],
)
def test_error_status_raises_service_error_with_detail(
    response: httpx.Response, message: str, exit_code: int
) -> None:
    with pytest.raises(ServiceError, match=message) as exc_info:
        client_for(lambda _: response).create(REQUEST)

    assert exc_info.value.exit_code == exit_code


def test_long_error_bodies_are_truncated() -> None:
    body = "x" * (MAX_DETAIL_LENGTH * 2)

    with pytest.raises(ServiceError) as exc_info:
        client_for(lambda _: httpx.Response(500, text=body)).get_output(PAYLOAD_ID)

    assert str(exc_info.value).endswith("x" * MAX_DETAIL_LENGTH)
    assert body not in str(exc_info.value)


def test_unexpected_success_body_is_a_service_error() -> None:
    with pytest.raises(ServiceError, match="unexpected response body"):
        client_for(lambda _: httpx.Response(201, json={"nope": 1})).create(REQUEST)


def test_transport_error_raises_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ServiceUnavailableError, match=f"cannot reach service at {BASE_URL}"):
        client_for(handler).create(REQUEST)


def test_timeout_is_reported_as_a_slow_service_not_an_unreachable_one() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(ServiceTimeoutError, match="did not answer in time") as exc_info:
        client_for(handler).get_output(PAYLOAD_ID)

    assert exc_info.value.exit_code == EXIT_FAILURE
