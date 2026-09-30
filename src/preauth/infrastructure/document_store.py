"""Private document bytes; opaque names, bounded uploads, no public static mount."""
import hashlib
from pathlib import Path
from uuid import uuid4, UUID

from preauth.domain.errors import NotFoundError, ValidationFailedError

MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
MEDIA_SIGNATURES = {
    "application/pdf": b"%PDF-",
    "image/png": b"\x89PNG\r\n\x1a\n",
    "image/jpeg": b"\xff\xd8\xff",
}


class DocumentStore:
    def __init__(self, root: str):
        self.root = Path(root).resolve()

    def save(self, content: bytes, media_type: str) -> tuple[str, str]:
        signature = MEDIA_SIGNATURES.get(media_type)
        if not content or len(content) > MAX_DOCUMENT_BYTES:
            raise ValidationFailedError("Upload a non-empty file of at most 10 MB", code="DOCUMENT_SIZE_INVALID")
        if signature is None or not content.startswith(signature):
            raise ValidationFailedError("Upload a PDF, PNG, or JPEG matching its file type", code="DOCUMENT_FORMAT_INVALID")
        self.root.mkdir(parents=True, exist_ok=True)
        key = str(uuid4())
        path = self.root / key
        try:
            with path.open("xb") as stream:
                stream.write(content)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return "document://" + key, hashlib.sha256(content).hexdigest()

    def path(self, uri: str) -> Path:
        try:
            key = uri.removeprefix("document://")
            if not uri.startswith("document://") or str(UUID(key)) != key:
                raise ValueError
        except ValueError:
            raise NotFoundError("Document content is not stored here", code="DOCUMENT_CONTENT_NOT_FOUND") from None
        path = self.root / key
        if not path.is_file() or path.is_symlink():
            raise NotFoundError("Document content is unavailable", code="DOCUMENT_CONTENT_NOT_FOUND")
        return path

    def remove(self, uri: str) -> None:
        self.path(uri).unlink(missing_ok=True)
