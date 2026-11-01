"""How a payload's output is built from transformed strings, and how strings are keyed."""

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from typing import Final

OUTPUT_SEPARATOR: Final = ", "


def interleave[T](first: Sequence[T], second: Sequence[T]) -> list[T]:
    """Return ``first[0], second[0], first[1], second[1], ...``.

    Raises ``ValueError`` if the lengths differ. Callers validate this too, but the domain
    must not rely on them for its own invariant.
    """
    return [item for pair in zip(first, second, strict=True) for item in pair]


def render_output(items: Iterable[str]) -> str:
    return OUTPUT_SEPARATOR.join(items)


def build_payload_output(
    list_1: Sequence[str],
    list_2: Sequence[str],
    transformed: Mapping[str, str],
) -> str:
    """Map each original string through ``transformed``, interleave the lists and render them.

    A string missing from ``transformed`` raises ``KeyError``: it means the cache layer lost a
    result, which must fail loudly rather than produce a payload with a gap.
    """
    return render_output(
        interleave([transformed[s] for s in list_1], [transformed[s] for s in list_2])
    )


def fingerprint(value: str) -> str:
    """Hex SHA-256 of the UTF-8 bytes of ``value``.

    A fixed-size key keeps database indexes small and portable whatever the input length.
    The exact string is hashed, with no trimming, case folding or Unicode normalization,
    because the transformer may treat any of those variants differently. Strict UTF-8, so a
    string that cannot be stored as text (a lone surrogate) fails here instead of getting a key.
    """
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
