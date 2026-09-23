"""النسخ الاحتياطي والتحقق والاستعادة وصحة قاعدة البيانات (FR-BK، 15-deployment §2).

صيغة النسخة: ملف ZIP يحتوي database.dump (pg_dump -Fc) ومجلد attachments/ وmanifest.json
(الإصدار، والوقت، وآخر Hash لسلسلة التدقيق، وبصمة كل ملف). يُشفَّر الملف كاملًا بـ AES-256-GCM
إذا ضُبط GBCFMS_BACKUP_KEY. سجل النسخ يُحفظ أيضًا في ملف history.jsonl بجوار النسخ حتى يبقى
بعد الاستعادة.
"""
import base64
import hashlib
import io
import json
import os
import subprocess  # noqa: S404 (أدوات PostgreSQL الرسمية بمعاملات ثابتة)
import tempfile
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_engine
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.backup.models import Backup, BackupSettings

MAGIC = b"GBCFMS-BAK1"


def _key() -> bytes | None:
    k = get_settings().backup_key
    if not k:
        return None
    raw = base64.b64decode(k)
    if len(raw) != 32:
        raise ValidationFailed("GBCFMS_BACKUP_KEY يجب أن يكون 32 بايت بترميز base64.", code="BAD_BACKUP_KEY")
    return raw


def _libpq() -> tuple[list[str], dict]:
    """معاملات الاتصال لأدوات pg_dump/pg_restore، وكلمة المرور عبر متغير بيئة لا عبر سطر الأوامر."""
    url = make_url(str(get_engine().url.render_as_string(hide_password=False)))
    env = {**os.environ, "PGPASSWORD": url.password or ""}
    args = ["-h", url.host or "localhost", "-p", str(url.port or 5432), "-U", url.username or "", "-d", url.database]
    return args, env


def settings_row(session: Session) -> BackupSettings:
    return session.get(BackupSettings, 1)


def _location(session: Session) -> Path:
    loc = Path(settings_row(session).location)
    if not loc.is_absolute():   # المسار النسبي يُحل داخل مجلد التخزين (storage/backups ← <storage_dir>/backups)
        rel = Path(*loc.parts[1:]) if loc.parts and loc.parts[0] == "storage" else loc
        loc = Path(get_settings().storage_dir).resolve() / rel
    loc.mkdir(parents=True, exist_ok=True)
    return loc


def _history(loc: Path, record: dict) -> None:
    with open(loc / "history.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def run_backup(session: Session, kind: str, requested_by: uuid.UUID | None) -> Backup:
    st = settings_row(session)
    loc = _location(session)
    b = Backup(id=uuid.uuid4(), kind=kind, status="RUNNING", location=str(loc), requested_by=requested_by)
    session.add(b)
    session.flush()
    try:
        args, env = _libpq()
        dump = subprocess.run(["pg_dump", "-Fc", "--no-owner", *args], capture_output=True, env=env, check=True,  # noqa: S603, S607
                              timeout=3600).stdout
        last_hash = session.scalar(text("SELECT row_hash FROM audit_log ORDER BY id DESC LIMIT 1"))
        head = session.scalar(text("SELECT version_num FROM alembic_version"))
        buf = io.BytesIO()
        files = {"database.dump": hashlib.sha256(dump).hexdigest()}
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("database.dump", dump)
            att_root = Path(get_settings().storage_dir).resolve() / "attachments"
            if st.include_attachments and att_root.exists():
                for fpath in sorted(att_root.rglob("*")):
                    if fpath.is_file():
                        data = fpath.read_bytes()
                        name = f"attachments/{fpath.relative_to(att_root).as_posix()}"
                        z.writestr(name, data)
                        files[name] = hashlib.sha256(data).hexdigest()
            manifest = {"format": "gbcfms-backup/1", "created_at": datetime.now(UTC).isoformat(), "schema": head,
                        "audit_last_hash": last_hash, "files": files, "kind": kind}
            z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
        payload = buf.getvalue()
        key = _key()
        if key:
            nonce = os.urandom(12)
            payload = MAGIC + nonce + AESGCM(key).encrypt(nonce, payload, MAGIC)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        name = f"gbcfms-{stamp}-{kind.lower()}-{b.id.hex[:8]}.bak{'.enc' if key else ''}"
        with open(loc / name, "xb") as f:
            f.write(payload)
        b.file_name, b.size_bytes, b.sha256 = name, len(payload), hashlib.sha256(payload).hexdigest()
        b.encrypted, b.manifest, b.status, b.finished_at = bool(key), manifest, "SUCCEEDED", datetime.now(UTC)
    except (subprocess.CalledProcessError, OSError) as e:
        b.status, b.finished_at = "FAILED", datetime.now(UTC)
        b.error = (getattr(e, "stderr", b"") or b"").decode(errors="replace")[-2000:] or str(e)
    session.flush()
    _history(loc, {"id": b.id, "kind": kind, "file": b.file_name, "sha256": b.sha256, "status": b.status,
                   "at": b.finished_at, "encrypted": b.encrypted, "size": b.size_bytes})
    if b.status == "SUCCEEDED":
        _apply_retention(session, st.retention_count, loc)
    return b


def _apply_retention(session: Session, keep: int, loc: Path) -> None:
    olds = list(session.scalars(select(Backup).where(Backup.status == "SUCCEEDED", Backup.kind != "PRE_RESTORE")
                                .order_by(Backup.started_at.desc())))[keep:]
    for b in olds:
        try:
            (loc / b.file_name).unlink(missing_ok=True)
        except OSError:
            continue
        b.status = "DELETED"
    session.flush()


def _open(b: Backup) -> tuple[zipfile.ZipFile, dict]:
    path = Path(b.location) / b.file_name
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != b.sha256:
        raise Conflict("بصمة ملف النسخة لا تطابق السجل (تالف أو معدّل).", code="BACKUP_CORRUPT")
    if data.startswith(MAGIC):
        key = _key()
        if key is None:
            raise ValidationFailed("النسخة مشفرة ولا يوجد مفتاح.", code="BACKUP_KEY_MISSING")
        nonce, ct = data[len(MAGIC):len(MAGIC) + 12], data[len(MAGIC) + 12:]
        data = AESGCM(key).decrypt(nonce, ct, MAGIC)
    z = zipfile.ZipFile(io.BytesIO(data))
    manifest = json.loads(z.read("manifest.json"))
    return z, manifest


def get(session: Session, backup_id: uuid.UUID) -> Backup:
    b = session.get(Backup, backup_id)
    if b is None or b.status not in ("SUCCEEDED",):
        raise NotFound("النسخة غير موجودة أو غير صالحة.")
    return b


def verify(session: Session, b: Backup) -> dict:
    """التحقق: البصمة، وفك التشفير، وبصمة كل ملف داخلها، وقابلية قراءة التفريغ بـ pg_restore --list."""
    result = {"ok": False}
    try:
        z, manifest = _open(b)
        bad = [n for n, h in manifest["files"].items() if hashlib.sha256(z.read(n)).hexdigest() != h]
        with tempfile.NamedTemporaryFile(suffix=".dump") as tmp:
            tmp.write(z.read("database.dump"))
            tmp.flush()
            listing = subprocess.run(["pg_restore", "--list", tmp.name], capture_output=True, check=True,  # noqa: S603, S607
                                     timeout=600).stdout.decode(errors="replace")
        result = {"ok": not bad, "corrupt_files": bad, "toc_entries": listing.count("\n"),
                  "schema": manifest.get("schema"), "files": len(manifest["files"])}
    except (Conflict, ValidationFailed) as e:
        result = {"ok": False, "error": e.message}
    except Exception as e:  # noqa: BLE001 — أي فشل في القراءة يعني نسخة غير صالحة
        result = {"ok": False, "error": str(e)[:500]}
    b.verified_at, b.verification = datetime.now(UTC), result
    session.flush()
    return result


def restore(b_id: uuid.UUID, confirm: str, actor: uuid.UUID) -> dict:
    """الاستعادة الكاملة (خطيرة): تأكيد باسم قاعدة البيانات، ونسخة PRE_RESTORE تلقائية قبلها.

    تعمل بجلسات مستقلة لأن قاعدة البيانات نفسها تُستبدل.
    """
    from app.core.db import AuditContext, new_session, set_audit_context
    args, env = _libpq()
    dbname = args[args.index("-d") + 1]
    if confirm != dbname:
        raise ValidationFailed("للتأكيد اكتب اسم قاعدة البيانات كما هو.", code="CONFIRMATION_MISMATCH",
                               details={"expected_hint": dbname[:3] + "…"})
    with new_session() as s:
        set_audit_context(s, AuditContext(user_id=actor, reason="نسخة تلقائية قبل الاستعادة"))
        b = get(s, b_id)
        z, manifest = _open(b)
        pre = run_backup(s, "PRE_RESTORE", actor)
        if pre.status != "SUCCEEDED":
            raise Conflict("تعذر أخذ نسخة احتياطية قبل الاستعادة؛ أُلغيت الاستعادة.", code="PRE_RESTORE_FAILED")
        s.commit()
        pre_info = {"id": str(pre.id), "file": pre.file_name, "sha256": pre.sha256}
        restored_info = {"id": str(b.id), "file": b.file_name, "sha256": b.sha256, "location": b.location,
                         "encrypted": b.encrypted, "manifest": b.manifest, "kind": b.kind}
        pre_row = {"location": pre.location, "encrypted": pre.encrypted, "manifest": pre.manifest,
                   "size": pre.size_bytes}
    get_engine().dispose()
    with tempfile.NamedTemporaryFile(suffix=".dump") as tmp:
        tmp.write(z.read("database.dump"))
        tmp.flush()
        subprocess.run(["pg_restore", "--clean", "--if-exists", "--no-owner", "--single-transaction", *args, tmp.name],  # noqa: S603, S607
                       capture_output=True, env=env, check=True, timeout=3600)
    att_root = Path(get_settings().storage_dir).resolve() / "attachments"
    for name in z.namelist():
        if name.startswith("attachments/"):
            target = (att_root / name.removeprefix("attachments/")).resolve()
            if att_root not in target.parents:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(name))
    get_engine().dispose()
    with new_session() as s:
        set_audit_context(s, AuditContext(user_id=actor, reason=f"استعادة النسخة {restored_info['file']}"))
        for info, row in ((pre_info, pre_row), (restored_info, restored_info)):
            if s.get(Backup, uuid.UUID(info["id"])) is None:
                s.add(Backup(id=uuid.UUID(info["id"]), kind="PRE_RESTORE" if info is pre_info else restored_info["kind"],
                             status="SUCCEEDED", location=row["location"], file_name=info["file"],
                             sha256=info["sha256"], encrypted=row["encrypted"], manifest=row["manifest"],
                             finished_at=datetime.now(UTC)))
        s.execute(text("SELECT audit_append('RESTORE', 'backups', :r, NULL, CAST(:d AS jsonb))"),
                  {"r": restored_info["id"], "d": json.dumps({"file": restored_info["file"], "pre_restore": pre_info})})
        s.commit()
    return {"restored": restored_info["file"], "pre_restore_backup": pre_info["file"], "schema": manifest.get("schema")}


def health(session: Session) -> dict:
    from app.modules.ledger import engine
    size = session.scalar(text("SELECT pg_size_pretty(pg_database_size(current_database()))"))
    tables = [dict(r._mapping) for r in session.execute(text(
        "SELECT relname AS table, n_live_tup AS rows, pg_size_pretty(pg_total_relation_size(relid)) AS size,"
        " last_autovacuum, last_vacuum FROM pg_stat_user_tables ORDER BY pg_total_relation_size(relid) DESC LIMIT 8"))]
    conns = session.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"))
    last = session.scalar(select(Backup).where(Backup.status == "SUCCEEDED").order_by(Backup.finished_at.desc()).limit(1))
    mismatched = engine.reconcile(session)
    return {"database_size": size, "connections": conns, "largest_tables": tables,
            "last_backup": {"at": last.finished_at, "file": last.file_name, "verified": (last.verification or {}).get("ok")}
            if last else None,
            "balances_reconciled": not mismatched, "encryption_configured": _key() is not None,
            "checked_at": datetime.now(UTC)}
