"""Authenticated document transfer; metadata registration remains in CaseService."""
from typing import Annotated

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from preauth.api.actor import ActorDep
from preauth.api.routes.cases import CaseId, _doc, PROVIDER_CHANNELS, ANY_ACTOR
from preauth.application.document_service import MAX_DOCUMENT_BYTES
from preauth.application.views import DocumentView
from preauth.domain.enums import DocumentType
from preauth.domain.errors import ValidationFailedError

router = APIRouter(prefix="/api/v1/cases", tags=["Provider interaction"])


@router.post("/{case_id}/documents/upload", status_code=status.HTTP_201_CREATED,
             summary="Upload and register a supporting document",
             description=_doc("Receives up to 10 MB of PDF, PNG or JPEG bytes and registers their hash and metadata in private storage.", PROVIDER_CHANNELS, "Editable states → INFORMATION_COLLECTION."))
async def upload_document(request: Request, actor: ActorDep, case_id: CaseId,
                          document_type: DocumentType,
                          title: Annotated[str, Query(min_length=1, max_length=200)]) -> DocumentView:
    service = request.app.state.documents
    await run_in_threadpool(service.validate_upload, case_id, actor)
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > MAX_DOCUMENT_BYTES:
            raise ValidationFailedError("File exceeds the 10 MB limit", code="DOCUMENT_SIZE_INVALID")
        content.extend(chunk)
    return await run_in_threadpool(service.upload, case_id, actor, document_type, title, bytes(content), media_type)


@router.get("/{case_id}/documents/{document_id}/content", summary="Download a registered supporting document",
            description=_doc("Returns privately stored content attached to this case as a download.", ANY_ACTOR, "None."))
def download_document(request: Request, actor: ActorDep, case_id: CaseId, document_id: str) -> FileResponse:
    path, media_type, filename = request.app.state.documents.download(case_id, actor, document_id)
    return FileResponse(path, media_type=media_type, filename=filename,
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
