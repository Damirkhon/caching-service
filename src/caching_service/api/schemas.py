"""Pydantic request and response models: the public HTTP contract and its input limits."""

import uuid
from typing import Annotated, Final, Literal, Self

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)
from pydantic.config import JsonDict
from pydantic_core import PydanticCustomError

# Bounded so one request can't monopolise the transformer, and so its at most 2000 distinct
# strings stay well inside the bind-parameter limits of SQLite and asyncpg.
MAX_ITEMS: Final = 1000
MAX_STRING_LENGTH: Final = 1000

SAMPLE_REQUEST: Final[JsonDict] = {
    "list_1": ["first string", "second string", "third string"],
    "list_2": ["other string", "another string", "last string"],
}
SAMPLE_OUTPUT: Final = (
    "FIRST STRING, OTHER STRING, SECOND STRING, ANOTHER STRING, THIRD STRING, LAST STRING"
)
SAMPLE_ID: Final = "3f2b8a4e-6c1d-4e7a-9b5f-2d8c1a0e4b7f"


def _storable_text(value: object) -> object:
    # The JSON parser accepts both of these, but neither can be stored: a lone surrogate has
    # no UTF-8 encoding to hash, and PostgreSQL text rejects NUL characters. Checked before
    # pydantic's own string validation so the rule doesn't depend on its internals.
    if not isinstance(value, str):
        return value
    if "\x00" in value:
        raise PydanticCustomError("nul_character", "Strings must not contain NUL characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise PydanticCustomError(
            "invalid_unicode", "Strings must be valid Unicode text (no lone surrogates)"
        ) from None
    return value


Item = Annotated[
    str,
    StringConstraints(min_length=1, max_length=MAX_STRING_LENGTH),
    BeforeValidator(_storable_text),
]
ItemList = Annotated[list[Item], Field(min_length=1, max_length=MAX_ITEMS)]


class PayloadCreateRequest(BaseModel):
    # Forbidding extras turns a typo such as "list1" into a clear error about the unknown
    # field, rather than a confusing one about a missing field.
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [SAMPLE_REQUEST]})

    list_1: ItemList
    list_2: ItemList

    @model_validator(mode="after")
    def _lists_have_equal_length(self) -> Self:
        if len(self.list_1) != len(self.list_2):
            raise PydanticCustomError(
                "unequal_lengths", "list_1 and list_2 must have the same length"
            )
        return self


class PayloadCreatedResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"examples": [{"id": SAMPLE_ID, "message": "Payload created."}]}
    )

    id: uuid.UUID
    message: str


class PayloadResponse(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"output": SAMPLE_OUTPUT}]})

    output: str


class ErrorResponse(BaseModel):
    detail: str


class HealthResponse(BaseModel):
    status: Literal["ok", "unavailable"]
