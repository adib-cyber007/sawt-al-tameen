"""Machine-readable error envelope and exception-to-HTTP mapping."""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from preauth.agent_tools.toolbox import ToolArgumentsInvalidError
from preauth.api.actor import ActorRequiredError
from preauth.domain.errors import (
    AuthorizationError,
    ConcurrencyConflictError,
    DomainError,
    IntegrityViolationError,
    InvalidStateTransitionError,
    NotFoundError,
    OperationNotAllowedError,
    ValidationFailedError,
)
from preauth.infrastructure.elevenlabs_signature import WebhookSignatureError
from preauth.infrastructure.observability import case_id_var, request_id_var

logger = logging.getLogger("preauth.api.errors")

STATUS_BY_ERROR: list[tuple[type[DomainError], int]] = [
    (ActorRequiredError, 401),
    (WebhookSignatureError, 401),
    (AuthorizationError, 403),
    (NotFoundError, 404),
    (InvalidStateTransitionError, 409),
    (OperationNotAllowedError, 409),
    (ConcurrencyConflictError, 409),
    (ValidationFailedError, 422),
    (ToolArgumentsInvalidError, 422),
    (IntegrityViolationError, 500),
]
# Registered by name to avoid importing route modules here.
STATUS_BY_CODE: dict[str, int] = {
    "CHANNEL_NOT_CONFIGURED": 503,
    "WEBHOOK_SIGNATURE_INVALID": 401,
    "WEBHOOK_ARTIFACT_PENDING": 503,
    "VOICE_PROVIDER_UNAVAILABLE": 503,
    "TWILIO_SIGNATURE_INVALID": 401,
    # Local mode: a missing model or a stopped service is an unavailable dependency, not a bad request.
    "LOCAL_DEPENDENCY_MISSING": 503,
    "LOCAL_SERVICE_UNAVAILABLE": 503,
    "SPEECH_INPUT_DISABLED": 503,
    "SPEECH_OUTPUT_DISABLED": 503,
    "SPEECH_NOT_RECOGNISED": 422,
    "CONVERSATION_NOT_FOUND": 404,
    "CONVERSATION_CLOSED": 409,
}


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any]
    request_id: str | None
    case_id: str | None


class ErrorResponse(BaseModel):
    error: ErrorBody


def error_envelope(status: int, code: str, message: str, details: dict[str, Any]) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorBody(
            code=code, message=message, details=details, request_id=request_id_var.get(), case_id=case_id_var.get()
        )
    )
    return JSONResponse(status_code=status, content=body.model_dump(mode="json"))


def _status_for(exc: DomainError) -> int:
    if exc.code in STATUS_BY_CODE:
        return STATUS_BY_CODE[exc.code]
    for cls, status in STATUS_BY_ERROR:
        if isinstance(exc, cls):
            return status
    return 400


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def domain_error(_: Request, exc: DomainError) -> JSONResponse:
        status = _status_for(exc)
        log = logger.error if status >= 500 else logger.warning
        log("request_failed", extra={"error_code": exc.code, "status_code": status, "details": exc.details})
        return error_envelope(status, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def request_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"location": list(e["loc"]), "message": e["msg"], "type": e["type"]} for e in exc.errors()
        ]
        logger.warning("request_rejected", extra={"error_code": "REQUEST_VALIDATION_FAILED", "errors": errors})
        return error_envelope(422, "REQUEST_VALIDATION_FAILED", "Request does not match the schema", {"errors": errors})


COMMON_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "ACTOR_REQUIRED / INVALID_ACTOR / GATEWAY_SECRET_INVALID"},
    422: {"model": ErrorResponse, "description": "REQUEST_VALIDATION_FAILED"},
    500: {"model": ErrorResponse, "description": "INTERNAL_ERROR / INTEGRITY_VIOLATION"},
}


def error_responses(**codes: str) -> dict[int | str, dict[str, Any]]:
    """Build an OpenAPI ``responses`` mapping, e.g. error_responses(r404="CASE_NOT_FOUND")."""
    responses = dict(COMMON_ERRORS)
    for key, description in codes.items():
        status = int(key.removeprefix("r"))
        if status in responses and status == 422:
            description = f"{responses[status]['description']} / {description}"
        responses[status] = {"model": ErrorResponse, "description": description}
    return responses
