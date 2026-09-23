"""أوامر الإدارة.

    python -m app.cli bootstrap --admin-username admin --admin-name "مسؤول النظام"
    python -m app.cli seed-reference
"""
import argparse
import getpass
import sys

from sqlalchemy import select

from app.core.db import SYSTEM_USER_ID, AuditContext, new_session, set_audit_context
from app.core.security import hash_password, validate_password_policy
from app.modules.users.models import User
from app.modules.users.service import set_roles, sync_permissions_and_roles


def bootstrap(username: str, full_name: str, password: str | None) -> None:
    with new_session() as s:
        set_audit_context(s, AuditContext(user_id=SYSTEM_USER_ID, reason="bootstrap"))
        sync_permissions_and_roles(s)
        from app.modules.workflow.definitions import sync_workflow_definitions
        sync_workflow_definitions(s)
        if s.scalar(select(User).where(User.username == username)) is None:
            password = password or getpass.getpass("كلمة مرور المسؤول: ")
            validate_password_policy(password, username)
            admin = User(username=username, full_name=full_name, password_hash=hash_password(password),
                         must_change_password=True)
            s.add(admin)
            s.flush()
            set_roles(s, admin, ["SYSTEM_ADMIN"])
            print(f"أُنشئ المسؤول «{username}» (يجب تغيير كلمة المرور عند أول دخول).")
        s.commit()
    print("تمت مزامنة الصلاحيات والأدوار.")


def seed_reference() -> None:
    from app.modules.catalog.seed import seed_reference_data
    with new_session() as s:
        set_audit_context(s, AuditContext(user_id=SYSTEM_USER_ID, reason="seed reference data"))
        created = seed_reference_data(s)
        s.commit()
    print(f"البيانات المرجعية: {created}")


def backup(kind: str = "MANUAL") -> None:
    from app.modules.backup.service import run_backup, verify
    with new_session() as s:
        set_audit_context(s, AuditContext(user_id=SYSTEM_USER_ID, reason=f"نسخة احتياطية ({kind})"))
        b = run_backup(s, kind, None)
        if b.status == "SUCCEEDED":
            verify(s, b)
        s.commit()
        print(f"{b.status}: {b.file_name} ({b.size_bytes} بايت) تحقق={(b.verification or {}).get('ok')}")


def scheduler() -> None:
    """مجدول بسيط لخادم محلي: نسخة يومية في الوقت المحدد، وتقييم التنبيهات كل ساعة."""
    import time
    from datetime import datetime

    from app.modules.alerts.service import run_periodic
    from app.modules.backup.service import settings_row
    last_backup_day, last_alerts_hour = None, None
    while True:
        now = datetime.now()
        with new_session() as s:
            st = settings_row(s)
            due = st.enabled and now.strftime("%H:%M") >= st.daily_time and last_backup_day != now.date()
        if due:
            backup("SCHEDULED")
            last_backup_day = now.date()
        if last_alerts_hour != (now.date(), now.hour):
            with new_session() as s:
                set_audit_context(s, AuditContext(user_id=SYSTEM_USER_ID, reason="تقييم دوري للتنبيهات"))
                run_periodic(s)
                s.commit()
            last_alerts_hour = (now.date(), now.hour)
        time.sleep(30)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gbcfms")
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("bootstrap", help="مزامنة الصلاحيات وإنشاء المسؤول الأول")
    b.add_argument("--admin-username", default="admin")
    b.add_argument("--admin-name", default="مسؤول النظام")
    b.add_argument("--admin-password", default=None, help="للأتمتة فقط؛ يُفضّل الإدخال التفاعلي")
    sub.add_parser("seed-reference", help="تحميل الباب الثاني وبنوده 2/1–2/29 والجهة الافتراضية")
    sub.add_parser("backup", help="نسخة احتياطية فورية مع التحقق")
    sub.add_parser("scheduler", help="تشغيل المجدول (نسخ يومي وتنبيهات كل ساعة)")
    args = parser.parse_args(argv)
    if args.cmd == "bootstrap":
        bootstrap(args.admin_username, args.admin_name, args.admin_password)
    elif args.cmd == "seed-reference":
        seed_reference()
    elif args.cmd == "backup":
        backup()
    elif args.cmd == "scheduler":
        scheduler()
    return 0


if __name__ == "__main__":
    sys.exit(main())
