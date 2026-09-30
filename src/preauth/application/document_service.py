"""Store supporting files and attach them to editable cases through the intake service."""
from pathlib import Path

from preauth.application.case_service import CaseService, require_intake_actor, require_editable
from preauth.application.query_service import CaseQueryService
from preauth.application.views import DocumentView
from preauth.domain.actors import Actor
from preauth.domain.enums import DocumentType
from preauth.application.commands import RegisterDocumentCommand
from preauth.domain.errors import NotFoundError
from preauth.infrastructure.document_store import DocumentStore, MAX_DOCUMENT_BYTES


class DocumentService:
    def __init__(self, cases: CaseService, queries: CaseQueryService, store: DocumentStore):
        self._cases, self._queries, self._store = cases, queries, store

    def validate_upload(self, case_id: str, actor: Actor) -> None:
        require_intake_actor(actor)
        require_editable(self._queries.get_case(case_id, actor))

    def upload(self, case_id: str, actor: Actor, document_type: DocumentType, title: str,
               content: bytes, media_type: str) -> DocumentView:
        self.validate_upload(case_id, actor)
        uri, digest = self._store.save(content, media_type)
        try:
            return self._cases.register_document(case_id, actor, RegisterDocumentCommand(
                document_type=document_type, title=title, storage_uri=uri,
                media_type=media_type, content_sha256=digest))
        except BaseException:
            self._store.remove(uri)
            raise

    def download(self, case_id: str, actor: Actor, document_id: str) -> tuple[Path, str, str]:
        case = self._queries.get_case(case_id, actor)
        document = next((doc for doc in case.documents if doc.id == document_id), None)
        if document is None:
            raise NotFoundError("Document is not attached to this case", code="DOCUMENT_NOT_FOUND")
        suffix = {"application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg"}.get(document.media_type, "")
        return self._store.path(document.storage_uri), document.media_type, document.id + suffix
