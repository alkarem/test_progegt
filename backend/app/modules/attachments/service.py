"""المرفقات الإلكترونية: فحص الملف، والتخزين، والربط بالمستندات، والقفل عند الترحيل (FR-DOC)."""
import hashlib
import io
import os
import re
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.attachments.models import Attachment, AttachmentLink

ALLOWED = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
CATEGORIES = {"INVOICE", "PAYMENT_ORDER", "DECISION", "CONTRACT", "AUTHORIZATION_LETTER", "CHEQUE", "RECEIPT",
              "IMPORT_SOURCE", "OTHER"}


def _detect(data: bytes, ext: str) -> str:
    """يتحقق من النوع الحقيقي للملف من محتواه (Magic bytes) وليس من الامتداد فقط (FR-DOC-04)."""
    if ext == ".pdf" and data.startswith(b"%PDF-"):
        return ALLOWED[ext]
    if ext == ".png" and data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ALLOWED[ext]
    if ext in (".jpg", ".jpeg") and data.startswith(b"\xff\xd8\xff"):
        return ALLOWED[ext]
    if ext in (".xlsx", ".docx") and data.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = set(z.namelist())
        except zipfile.BadZipFile as e:
            raise ValidationFailed("ملف Office تالف.", code="INVALID_FILE") from e
        if "[Content_Types].xml" not in names:
            raise ValidationFailed("ملف Office غير صالح.", code="INVALID_FILE")
        if any(n.lower().endswith("vbaproject.bin") for n in names):
            raise ValidationFailed("ملفات Office التي تحتوي وحدات ماكرو غير مسموحة.", code="MACRO_NOT_ALLOWED")
        expected = "xl/workbook.xml" if ext == ".xlsx" else "word/document.xml"
        if expected not in names:
            raise ValidationFailed("محتوى الملف لا يطابق امتداده.", code="TYPE_MISMATCH")
        return ALLOWED[ext]
    raise ValidationFailed("نوع الملف غير مسموح أو لا يطابق محتواه. المسموح: PDF، PNG، JPG، XLSX، DOCX.",
                           code="FILE_TYPE_NOT_ALLOWED")


def _safe_name(name: str) -> str:
    base = os.path.basename(name or "file").replace("\x00", "")
    base = re.sub(r"[\\/:*?\"<>|\r\n\t]", "_", base).strip(" .") or "file"
    return base[:200]


def storage_root() -> Path:
    root = Path(get_settings().storage_dir).resolve() / "attachments"
    root.mkdir(parents=True, exist_ok=True)
    return root


def save(session: Session, *, filename: str, data: bytes, uploaded_by: uuid.UUID, category: str | None,
         description: str | None) -> Attachment:
    settings = get_settings()
    if not data:
        raise ValidationFailed("الملف فارغ.", code="EMPTY_FILE")
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise ValidationFailed(f"حجم الملف يتجاوز {settings.max_upload_mb} ميجابايت.", code="FILE_TOO_LARGE")
    if category and category not in CATEGORIES:
        raise ValidationFailed("تصنيف المرفق غير معروف.", code="UNKNOWN_CATEGORY")
    name = _safe_name(filename)
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED:
        raise ValidationFailed("نوع الملف غير مسموح.", code="FILE_TYPE_NOT_ALLOWED")
    mime = _detect(data, ext)
    digest = hashlib.sha256(data).hexdigest()
    now = datetime.now(UTC)
    att_id = uuid.uuid4()
    key = f"{now:%Y/%m}/{att_id.hex}{ext}"   # اسم عشوائي؛ الاسم الأصلي في قاعدة البيانات فقط
    path = storage_root() / key
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "xb") as f:
        f.write(data)
    att = Attachment(id=att_id, storage_key=key, original_filename=name, mime_type=mime, size_bytes=len(data),
                     sha256=digest, category=category, description=description, uploaded_by=uploaded_by)
    session.add(att)
    session.flush()
    return att


def get(session: Session, attachment_id: uuid.UUID) -> Attachment:
    a = session.get(Attachment, attachment_id)
    if a is None:
        raise NotFound("المرفق غير موجود.")
    return a


def read_bytes(att: Attachment) -> bytes:
    path = (storage_root() / att.storage_key).resolve()
    if storage_root() not in path.parents:
        raise NotFound("المرفق غير موجود.")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != att.sha256:
        raise Conflict("فشل التحقق من سلامة الملف المخزن (البصمة لا تطابق).", code="INTEGRITY_FAILURE")
    return data


def link(session: Session, att: Attachment, source_type: str, source_id: uuid.UUID, user_id: uuid.UUID) -> None:
    exists_ = session.get(AttachmentLink, (att.id, source_type, source_id))
    if exists_:
        return
    session.add(AttachmentLink(attachment_id=att.id, source_type=source_type, source_id=source_id,
                               linked_by=user_id))
    session.flush()


def unlink(session: Session, att: Attachment, source_type: str, source_id: uuid.UUID) -> None:
    ln = session.get(AttachmentLink, (att.id, source_type, source_id))
    if ln is None:
        raise NotFound("الربط غير موجود.")
    if ln.is_locked:
        raise Conflict("المرفق مقفل لارتباطه بمستند مرحّل.", code="ATTACHMENT_LOCKED")
    session.delete(ln)
    session.flush()


def for_source(session: Session, source_type: str, source_id: uuid.UUID) -> list[tuple[Attachment, AttachmentLink]]:
    return list(session.execute(select(Attachment, AttachmentLink).join(
        AttachmentLink, AttachmentLink.attachment_id == Attachment.id).where(
        AttachmentLink.source_type == source_type, AttachmentLink.source_id == source_id)
        .order_by(AttachmentLink.linked_at)).tuples())


def count_for_source(session: Session, source_type: str, source_id: uuid.UUID) -> int:
    return len(for_source(session, source_type, source_id))


def lock_for_source(session: Session, source_type: str, source_id: uuid.UUID) -> None:
    """عند ترحيل المستند: تُقفل مرفقاته فلا تُحذف ولا تُستبدل (FR-DOC-03)."""
    ids = [a.id for a, _ in for_source(session, source_type, source_id)]
    if not ids:
        return
    session.execute(update(AttachmentLink).where(AttachmentLink.source_type == source_type,
                                                 AttachmentLink.source_id == source_id).values(is_locked=True))
    session.execute(update(Attachment).where(Attachment.id.in_(ids)).values(is_locked=True))
    session.flush()
