"""The client's own copy of the service's HTTP contract.

Deliberately duplicated rather than imported, so the CLI stays installable and runnable without
the server package. Only the shape is checked here; the service owns the limits.
"""

import uuid
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator
from pydantic_core import PydanticCustomError


class PayloadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    list_1: list[str]
    list_2: list[str]

    @model_validator(mode="after")
    def _lists_have_equal_length(self) -> Self:
        if len(self.list_1) != len(self.list_2):
            raise PydanticCustomError(
                "unequal_lengths", "list_1 and list_2 must have the same length"
            )
        return self


class CreatedPayload(BaseModel):
    id: uuid.UUID
    message: str


class PayloadOutput(BaseModel):
    output: str
