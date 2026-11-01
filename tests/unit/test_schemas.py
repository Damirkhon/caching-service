from typing import Any

import pytest
from pydantic import ValidationError

from caching_service.api.schemas import (
    MAX_ITEMS,
    MAX_STRING_LENGTH,
    SAMPLE_REQUEST,
    PayloadCreateRequest,
)

INVALID_BODIES: list[tuple[str, Any]] = [
    ("unequal-lengths", {"list_1": ["a", "b"], "list_2": ["c"]}),
    ("empty-lists", {"list_1": [], "list_2": []}),
    ("empty-string", {"list_1": [""], "list_2": ["b"]}),
    ("non-string-item", {"list_1": [1], "list_2": ["b"]}),
    ("null-item", {"list_1": [None], "list_2": ["b"]}),
    ("string-too-long", {"list_1": ["x" * (MAX_STRING_LENGTH + 1)], "list_2": ["b"]}),
    ("too-many-items", {"list_1": ["a"] * (MAX_ITEMS + 1), "list_2": ["b"] * (MAX_ITEMS + 1)}),
    ("unknown-field", {"list_1": ["a"], "list_2": ["b"], "list_3": ["c"]}),
    ("missing-field", {"list_1": ["a"]}),
    ("not-a-list", {"list_1": "a", "list_2": "b"}),
    ("lone-surrogate", {"list_1": ["\ud800"], "list_2": ["b"]}),
    ("nul-character", {"list_1": ["a\x00b"], "list_2": ["b"]}),
]


@pytest.mark.parametrize(("case", "body"), INVALID_BODIES, ids=[c for c, _ in INVALID_BODIES])
def test_invalid_bodies_are_rejected(case: str, body: Any) -> None:
    with pytest.raises(ValidationError):
        PayloadCreateRequest.model_validate(body)


def test_sample_request_is_valid() -> None:
    request = PayloadCreateRequest.model_validate(SAMPLE_REQUEST)

    assert request.list_1 == SAMPLE_REQUEST["list_1"]
    assert request.list_2 == SAMPLE_REQUEST["list_2"]


def test_largest_allowed_request_is_valid() -> None:
    item = "x" * MAX_STRING_LENGTH

    request = PayloadCreateRequest.model_validate(
        {"list_1": [item] * MAX_ITEMS, "list_2": [item] * MAX_ITEMS}
    )

    assert len(request.list_1) == len(request.list_2) == MAX_ITEMS


def test_strings_are_kept_exactly() -> None:
    # No trimming or normalization: the cache keys on the exact string.
    request = PayloadCreateRequest.model_validate({"list_1": [" a "], "list_2": ["É"]})

    assert (request.list_1, request.list_2) == ([" a "], ["É"])


@pytest.mark.parametrize(
    ("body", "error_type"),
    [
        ({"list_1": ["a", "b"], "list_2": ["c"]}, "unequal_lengths"),
        ({"list_1": ["\ud800"], "list_2": ["b"]}, "invalid_unicode"),
        ({"list_1": ["a\x00"], "list_2": ["b"]}, "nul_character"),
    ],
)
def test_custom_rules_report_a_stable_error_type(body: Any, error_type: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        PayloadCreateRequest.model_validate(body)

    assert [error["type"] for error in exc_info.value.errors()] == [error_type]
