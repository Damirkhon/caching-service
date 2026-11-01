import asyncio
import logging
import uuid
from typing import Any

import pytest

from caching_service.api.exception_handlers import MAX_ECHOED_LOCATION_LENGTH
from caching_service.api.middleware import MAX_REQUEST_BYTES
from caching_service.api.schemas import MAX_ITEMS, MAX_STRING_LENGTH, SAMPLE_OUTPUT, SAMPLE_REQUEST
from caching_service.errors import TransformerError
from caching_service.services import transformation_cache
from tests.factories import api_client
from tests.fakes import (
    BlockingTransformer,
    CountingTransformer,
    FailingTransformer,
    ObservedSingleFlight,
)

# The engine fixture creates the schema; each app then opens its own engine on the same file.
pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("engine")]

SAMPLE_STRINGS = sorted([*SAMPLE_REQUEST["list_1"], *SAMPLE_REQUEST["list_2"]])  # type: ignore[misc]  # the sample is typed as generic JSON


async def test_create_payload_returns_201_and_location(database_url: str) -> None:
    async with api_client(database_url, CountingTransformer()) as client:
        response = await client.post("/payload", json=SAMPLE_REQUEST)

    assert response.status_code == 201
    body = response.json()
    assert body["message"] == "Payload created."
    assert response.headers["location"] == f"/payload/{uuid.UUID(body['id'])}"


async def test_location_keeps_the_prefix_a_proxy_mounts_the_app_under(database_url: str) -> None:
    async with api_client(database_url, CountingTransformer(), root_path="/api") as client:
        response = await client.post("/payload", json=SAMPLE_REQUEST)

    assert response.headers["location"] == f"/api/payload/{response.json()['id']}"


async def test_get_payload_returns_sample_output(database_url: str) -> None:
    async with api_client(database_url, CountingTransformer()) as client:
        created = await client.post("/payload", json=SAMPLE_REQUEST)
        response = await client.get(created.headers["location"])

    assert response.status_code == 200
    assert response.json() == {"output": SAMPLE_OUTPUT}


async def test_repeat_create_returns_200_same_id_without_transformer_calls(
    database_url: str,
) -> None:
    async with api_client(database_url, CountingTransformer()) as client:
        first = await client.post("/payload", json=SAMPLE_REQUEST)

    transformer = CountingTransformer()
    async with api_client(database_url, transformer) as client:
        second = await client.post("/payload", json=SAMPLE_REQUEST)

    assert second.status_code == 200
    assert second.json() == {"id": first.json()["id"], "message": "Payload already exists."}
    assert second.headers["location"] == first.headers["location"]
    assert transformer.calls == []


async def test_overlapping_input_only_transforms_new_strings(database_url: str) -> None:
    transformer = CountingTransformer()
    async with api_client(database_url, transformer) as client:
        await client.post("/payload", json=SAMPLE_REQUEST)
        transformer.calls.clear()
        response = await client.post(
            "/payload",
            json={"list_1": ["first string", "new one"], "list_2": ["other string", "new two"]},
        )

    assert response.status_code == 201
    assert sorted(transformer.calls) == ["new one", "new two"]


INVALID_BODIES: list[tuple[str, Any]] = [
    ("unequal-lengths", {"list_1": ["a", "b"], "list_2": ["c"]}),
    ("empty-lists", {"list_1": [], "list_2": []}),
    ("empty-string", {"list_1": [""], "list_2": ["b"]}),
    ("non-string-item", {"list_1": [1], "list_2": ["b"]}),
    ("string-too-long", {"list_1": ["x" * (MAX_STRING_LENGTH + 1)], "list_2": ["b"]}),
    ("too-many-items", {"list_1": ["a"] * (MAX_ITEMS + 1), "list_2": ["b"] * (MAX_ITEMS + 1)}),
    ("unknown-field", {"list_1": ["a"], "list_2": ["b"], "list1": ["c"]}),
    ("missing-field", {"list_1": ["a"]}),
]
# Sent as raw bytes: these are JSON-level cases a client library would not produce.
INVALID_RAW_BODIES: list[tuple[str, bytes]] = [
    ("invalid-json", b'{"list_1": ["a"], "list_2": '),
    ("lone-surrogate", b'{"list_1": ["\\ud800"], "list_2": ["b"]}'),
    ("nul-character", b'{"list_1": ["a\\u0000b"], "list_2": ["b"]}'),
]


@pytest.mark.parametrize(("case", "body"), INVALID_BODIES, ids=[c for c, _ in INVALID_BODIES])
async def test_create_payload_invalid_body_returns_422(
    database_url: str, case: str, body: Any
) -> None:
    transformer = CountingTransformer()
    async with api_client(database_url, transformer) as client:
        response = await client.post("/payload", json=body)

    assert response.status_code == 422
    assert transformer.calls == []


@pytest.mark.parametrize(
    ("case", "raw"), INVALID_RAW_BODIES, ids=[c for c, _ in INVALID_RAW_BODIES]
)
async def test_create_payload_invalid_raw_body_returns_422(
    database_url: str, case: str, raw: bytes
) -> None:
    transformer = CountingTransformer()
    async with api_client(database_url, transformer) as client:
        response = await client.post(
            "/payload", content=raw, headers={"content-type": "application/json"}
        )

    assert response.status_code == 422
    assert transformer.calls == []


async def test_validation_errors_do_not_echo_the_input(database_url: str) -> None:
    rejected = "x" * (MAX_STRING_LENGTH + 1)
    async with api_client(database_url, CountingTransformer()) as client:
        response = await client.post("/payload", json={"list_1": [rejected], "list_2": ["b"]})

    assert response.status_code == 422
    (error,) = response.json()["detail"]
    assert error["loc"] == ["body", "list_1", 0]
    assert "input" not in error
    assert rejected not in response.text


async def test_validation_errors_truncate_client_chosen_keys(database_url: str) -> None:
    unknown_key = "k" * 1000
    async with api_client(database_url, CountingTransformer()) as client:
        response = await client.post(
            "/payload", json={"list_1": ["a"], "list_2": ["b"], unknown_key: ["c"]}
        )

    assert response.status_code == 422
    (error,) = response.json()["detail"]
    assert error["type"] == "extra_forbidden"
    assert error["loc"] == ["body", "k" * MAX_ECHOED_LOCATION_LENGTH]


async def test_get_unknown_payload_returns_404(database_url: str) -> None:
    async with api_client(database_url, CountingTransformer()) as client:
        response = await client.get(f"/payload/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.json() == {"detail": "Payload not found."}


async def test_get_malformed_id_returns_422(database_url: str) -> None:
    async with api_client(database_url, CountingTransformer()) as client:
        response = await client.get("/payload/not-a-uuid")

    assert response.status_code == 422


async def test_transformer_failure_returns_502_and_keeps_partial_cache(database_url: str) -> None:
    body = {"list_1": ["ok"], "list_2": ["bad"]}
    async with api_client(database_url, FailingTransformer(fail_on={"bad"})) as client:
        failed = await client.post("/payload", json=body)

    retry = CountingTransformer()
    async with api_client(database_url, retry) as client:
        retried = await client.post("/payload", json=body)

    assert failed.status_code == 502
    assert failed.json() == {"detail": "Transformer service failed; please retry."}
    assert retried.status_code == 201
    assert retry.calls == ["bad"]


async def test_transformer_failure_is_logged_as_a_warning_with_its_cause(
    database_url: str, caplog: pytest.LogCaptureFixture
) -> None:
    body = {"list_1": ["ok"], "list_2": ["bad"]}
    async with api_client(database_url, FailingTransformer(fail_on={"bad"})) as client:
        with caplog.at_level(logging.WARNING, logger="caching_service.api.exception_handlers"):
            await client.post("/payload", json=body)

    (record,) = caplog.records
    assert record.levelno == logging.WARNING
    assert record.getMessage() == "transformer failure on POST /payload"
    assert record.exc_info is not None
    assert isinstance(record.exc_info[1], TransformerError)


async def test_unexpected_error_returns_500_without_details(database_url: str) -> None:
    buggy = FailingTransformer(fail_on={"bug"}, error=RuntimeError)
    async with api_client(database_url, buggy, raise_app_exceptions=False) as client:
        response = await client.post("/payload", json={"list_1": ["bug"], "list_2": ["b"]})

    assert response.status_code == 500
    assert response.text == "Internal Server Error"


async def test_concurrent_identical_requests_call_transformer_once_per_string(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    flights = ObservedSingleFlight()
    # The app builds its cache in the lifespan; this hands it an observable single-flight.
    # Safe because the cache only calls SingleFlight() at runtime; the name also appears in an
    # attribute annotation, but those are never evaluated inside a function body.
    monkeypatch.setattr(transformation_cache, "SingleFlight", lambda: flights)
    transformer = BlockingTransformer()

    async with api_client(database_url, transformer) as client:
        requests = [
            asyncio.create_task(client.post("/payload", json=SAMPLE_REQUEST)) for _ in range(2)
        ]
        # Every string of both requests has joined a shared call before any call returns.
        await flights.wait_for_callers(2 * len(SAMPLE_STRINGS))
        transformer.release.set()
        responses = await asyncio.gather(*requests)

    assert sorted(transformer.calls) == SAMPLE_STRINGS
    assert sorted(r.status_code for r in responses) == [200, 201]
    assert len({r.json()["id"] for r in responses}) == 1


async def test_openapi_documents_examples_and_responses(database_url: str) -> None:
    async with api_client(database_url, CountingTransformer()) as client:
        schema = (await client.get("/openapi.json")).json()

    create = schema["paths"]["/payload"]["post"]
    read = schema["paths"]["/payload/{payload_id}"]["get"]
    assert {"200", "201", "422", "502"} <= set(create["responses"])
    assert {"200", "404", "422"} <= set(read["responses"])
    request_schema = schema["components"]["schemas"]["PayloadCreateRequest"]
    assert request_schema["examples"] == [SAMPLE_REQUEST]


async def test_oversized_body_returns_413(database_url: str) -> None:
    transformer = CountingTransformer()
    async with api_client(database_url, transformer) as client:
        response = await client.post(
            "/payload",
            content=b" " * (MAX_REQUEST_BYTES + 1),
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 413
    assert transformer.calls == []


async def test_deeply_nested_json_is_rejected_without_a_server_error(database_url: str) -> None:
    # Small enough to pass the size limit, deep enough to exhaust the JSON parser's recursion.
    nested = b"[" * 100_000
    async with api_client(database_url, CountingTransformer()) as client:
        response = await client.post(
            "/payload", content=nested, headers={"content-type": "application/json"}
        )

    assert response.status_code == 400
