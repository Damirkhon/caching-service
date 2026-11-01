"""Request dependencies: hand routes the app-scoped services built in the lifespan."""

from typing import Annotated

from fastapi import Depends, Request

from caching_service.services.health import HealthService
from caching_service.services.payloads import PayloadService


def get_payload_service(request: Request) -> PayloadService:
    service: PayloadService = request.app.state.payload_service
    return service


PayloadServiceDep = Annotated[PayloadService, Depends(get_payload_service)]


def get_health_service(request: Request) -> HealthService:
    service: HealthService = request.app.state.health_service
    return service


HealthServiceDep = Annotated[HealthService, Depends(get_health_service)]
