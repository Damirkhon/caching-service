import re
import unicodedata

import pytest

from caching_service.domain.payload import (
    build_payload_output,
    fingerprint,
    interleave,
    render_output,
)

SHA256_HEX = re.compile(r"[0-9a-f]{64}")


def uppercase_mapping(*lists: list[str]) -> dict[str, str]:
    return {s: s.upper() for strings in lists for s in strings}


def test_interleave_alternates_items() -> None:
    assert interleave([1, 3, 5], [2, 4, 6]) == [1, 2, 3, 4, 5, 6]


def test_interleave_single_pair() -> None:
    assert interleave(["a"], ["b"]) == ["a", "b"]


@pytest.mark.parametrize(("first", "second"), [(["a", "b"], ["c"]), (["a"], ["b", "c"])])
def test_interleave_rejects_unequal_lengths(first: list[str], second: list[str]) -> None:
    with pytest.raises(ValueError, match="zip"):
        interleave(first, second)


def test_render_output_joins_with_separator() -> None:
    assert render_output(["A", "B", "C"]) == "A, B, C"


def test_build_payload_output_matches_task_sample() -> None:
    list_1 = ["first string", "second string", "third string"]
    list_2 = ["other string", "another string", "last string"]

    output = build_payload_output(list_1, list_2, uppercase_mapping(list_1, list_2))

    assert output == (
        "FIRST STRING, OTHER STRING, SECOND STRING, ANOTHER STRING, THIRD STRING, LAST STRING"
    )


def test_build_payload_output_preserves_duplicates_and_order() -> None:
    list_1 = ["a", "a"]
    list_2 = ["b", "a"]

    output = build_payload_output(list_1, list_2, uppercase_mapping(list_1, list_2))

    assert output == "A, B, A, A"


def test_build_payload_output_missing_mapping_raises() -> None:
    with pytest.raises(KeyError) as exc_info:
        build_payload_output(["a"], ["b"], {"a": "A"})

    assert exc_info.value.args == ("b",)


def test_fingerprint_is_stable_hex_sha256() -> None:
    digest = fingerprint("first string")

    assert SHA256_HEX.fullmatch(digest)
    assert digest == fingerprint("first string")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("first string", "0cc1182178b8aa2db9a7b43f7db5d4ded695c60caecdbd0520a556216e2b2422"),
        # Non-ASCII, so a switch to another ASCII-compatible encoding is caught too.
        (
            unicodedata.normalize("NFC", "café"),
            "850f7dc43910ff890f8879c0ed26fe697c93a067ad93a7d50f466a7028a9bf4e",
        ),
    ],
)
def test_fingerprint_digest_is_pinned(value: str, expected: str) -> None:
    # Stored cache keys depend on the exact algorithm and encoding.
    assert fingerprint(value) == expected


def test_fingerprint_rejects_lone_surrogate() -> None:
    with pytest.raises(UnicodeEncodeError):
        fingerprint("\ud800")


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("a", "a "),
        ("a", "A"),
        (unicodedata.normalize("NFC", "é"), unicodedata.normalize("NFD", "é")),
    ],
    ids=["trailing-whitespace", "case", "unicode-normalization"],
)
def test_fingerprint_distinguishes_near_duplicates(left: str, right: str) -> None:
    assert left != right
    assert fingerprint(left) != fingerprint(right)
