"""Payload endpoints: validate, delegate to the payload service, shape the response."""

import uuid
from typing import Final

from fastapi import APIRouter, Request, Response, status

from caching_service.api.dependencies import HealthServiceDep, PayloadServiceDep
from caching_service.api.schemas import (
    ErrorResponse,
    HealthResponse,
    PayloadCreatedResponse,
    PayloadCreateRequest,
    PayloadResponse,
)

PAYLOAD_CREATED: Final = "Payload created."
PAYLOAD_EXISTS: Final = "Payload already exists."

router = APIRouter()


@router.post(
    "/payload",
    status_code=status.HTTP_201_CREATED,
    response_model=PayloadCreatedResponse,
    summary="Create a payload, or reuse the id of an identical one",
    responses={
        status.HTTP_201_CREATED: {"description": "A new payload was generated and stored."},
        status.HTTP_200_OK: {
            "model": PayloadCreatedResponse,
            "description": "A payload with the same output already existed; its id is reused.",
        },
        status.HTTP_502_BAD_GATEWAY: {
            "model": ErrorResponse,
            "description": "The transformer failed or timed out; retrying may succeed.",
        },
    },
)
async def create_payload(
    body: PayloadCreateRequest,
    service: PayloadServiceDep,
    request: Request,
    response: Response,
) -> PayloadCreatedResponse:
    result = await service.create(body.list_1, body.list_2)
    # A relative path, built from url_for so it keeps any prefix a proxy mounts the app under.
    response.headers["Location"] = request.url_for("get_payload", payload_id=str(result.id)).path
    # 201 versus 200 tells clients whether work was done; the body has the same shape either
    # way, so a client that only reads the id needs no special case.
    if not result.created:
        response.status_code = status.HTTP_200_OK
        return PayloadCreatedResponse(id=result.id, message=PAYLOAD_EXISTS)
    return PayloadCreatedResponse(id=result.id, message=PAYLOAD_CREATED)


@router.get(
    "/payload/{payload_id}",
    name="get_payload",
    response_model=PayloadResponse,
    summary="Read a payload's output",
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse, "description": "No such payload."}
    },
)
async def get_payload(payload_id: uuid.UUID, service: PayloadServiceDep) -> PayloadResponse:
    return PayloadResponse(output=await service.get_output(payload_id))


# HEAD too, because some load balancers and probes check with it; hidden from the schema so
# the documented operation stays a single GET.
@router.head("/health", include_in_schema=False)
@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Readiness check, including database connectivity",
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": HealthResponse,
            "description": "The database is unreachable.",
        }
    },
)
async def health(service: HealthServiceDep, response: Response) -> HealthResponse:
    if await service.database_is_reachable():
        return HealthResponse(status="ok")
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(status="unavailable")
