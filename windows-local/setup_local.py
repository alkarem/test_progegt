"""تثبيت النظام مباشرة على Windows دون Docker (PostgreSQL وPython مثبتان مسبقًا).

يعمل بمكتبة Python القياسية فقط:
1. يجد PostgreSQL (psql وpg_dump) ويتحقق من الإصدار.
2. ينشئ بيئة Python معزولة ويثبت اعتماديات الخادم.
3. ينشئ مستخدم قاعدة بيانات «gbcfms» بكلمة مرور عشوائية وقاعدة «gbcfms» (مرة واحدة).
4. يولّد الأسرار ويكتب backend/.env.
5. يرحّل المخطط، وينشئ المسؤول الأول، ويحمّل الباب الثاني وبنوده.
إعادة تشغيله آمنة: لا يعيد إنشاء ما هو موجود.
"""
import getpass
import os
import re
import secrets
import shutil
import subprocess
import sys
import tomllib
from base64 import b64encode
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
ENV_FILE = BACKEND / ".env"
VENV = BACKEND / ".venv"
WIN = os.name == "nt"
VPY = VENV / ("Scripts/python.exe" if WIN else "bin/python")
WEB = ROOT / "windows-local" / "web"

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass


def step(n, msg):
    print(f"\n[{n}/6] {msg}", flush=True)


def fail(msg):
    print(f"\n!! {msg}\n", flush=True)
    sys.exit(1)


def find_pg_bin() -> Path:
    found = shutil.which("psql")
    if found:
        return Path(found).parent
    candidates = []
    for base in filter(None, [os.environ.get("ProgramFiles"), os.environ.get("ProgramW6432"), "C:/Program Files"]):
        for d in Path(base, "PostgreSQL").glob("*"):
            if (d / "bin" / "psql.exe").is_file() and re.match(r"^\d+", d.name):
                candidates.append((float(re.match(r"^\d+(\.\d+)?", d.name).group()), d / "bin"))
    if not candidates:
        fail("لم يُعثر على PostgreSQL. ثبّته من https://www.postgresql.org/download/windows/ (الإصدار 13 أو أحدث).")
    return max(candidates)[1]


def exe(pg_bin: Path, name: str) -> str:
    return str(pg_bin / (name + (".exe" if WIN else "")))


def read_env() -> dict:
    out = {}
    if ENV_FILE.is_file():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def psql(pg_bin, conn, sql, db="postgres"):
    r = subprocess.run([exe(pg_bin, "psql"), "-h", conn["host"], "-p", conn["port"], "-U", conn["user"], "-d", db,
                        "-v", "ON_ERROR_STOP=1", "-tAc", sql],
                       env={**os.environ, "PGPASSWORD": conn["password"], "PGCLIENTENCODING": "UTF8"},
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip())
    return r.stdout.strip()


def _ask(prompt: str, default: str, digits: bool = False) -> str:
    while True:
        v = input(f"  {prompt} — اضغط Enter لاستخدام «{default}»: ").strip() or default
        if not digits or v.isdigit():
            return v
        print(f"  «{v}» ليس رقمًا. المنفذ رقم مثل 5432.")


def _explain(err: str) -> str:
    low = err.lower()
    if "password authentication failed" in low or "no password supplied" in low:
        return "كلمة المرور غير صحيحة لهذا المستخدم."
    if "does not exist" in low and "role" in low:
        return "اسم المستخدم غير موجود في PostgreSQL."
    if "connection refused" in low or "could not connect" in low or "timeout" in low:
        return "لا يوجد PostgreSQL يعمل على هذا المنفذ. تأكد أن خدمة postgresql تعمل (services.msc) وأن المنفذ صحيح."
    return err.splitlines()[-1] if err else "خطأ غير معروف"


def setup_database(pg_bin: Path) -> dict:
    print("  يحتاج المثبت حساب مسؤول PostgreSQL الذي أنشأته عند تثبيته، ليُنشئ مستخدمًا وقاعدة بيانات خاصين بالنظام.")
    print("  (في أغلب الأجهزة: المنفذ 5432 واسم المستخدم postgres — اضغط Enter لقبولهما)")
    conn = {"host": "localhost", "port": "5432", "user": "postgres"}
    version = None
    for attempt in range(4):
        conn["port"] = _ask("منفذ PostgreSQL (رقم)", conn["port"], digits=True)
        conn["user"] = _ask("اسم مستخدم مسؤول PostgreSQL", conn["user"])
        conn["password"] = getpass.getpass("  كلمة مرور هذا المستخدم (لا تظهر أثناء الكتابة): ")
        try:
            version = int(psql(pg_bin, conn, "SHOW server_version_num"))
            break
        except RuntimeError as e:
            print(f"\n  تعذر الاتصال: {_explain(str(e))}")
            if attempt < 3:
                print("  حاول مجددًا (القيم السابقة معروضة كافتراضية):\n")
    if version is None:
        fail("تعذر الاتصال بـ PostgreSQL. تأكد أن خدمة postgresql تعمل وأن البيانات صحيحة، ثم أعد تشغيل المثبت.")
    if version < 130000:
        fail(f"إصدار PostgreSQL قديم ({version // 10000}). المطلوب 13 أو أحدث.")

    password = secrets.token_hex(24)
    if psql(pg_bin, conn, "SELECT 1 FROM pg_roles WHERE rolname = 'gbcfms'"):
        psql(pg_bin, conn, f"ALTER ROLE gbcfms WITH LOGIN PASSWORD '{password}'")
    else:
        psql(pg_bin, conn, f"CREATE ROLE gbcfms WITH LOGIN PASSWORD '{password}'")
    if not psql(pg_bin, conn, "SELECT 1 FROM pg_database WHERE datname = 'gbcfms'"):
        psql(pg_bin, conn, "CREATE DATABASE gbcfms OWNER gbcfms ENCODING 'UTF8' TEMPLATE template0")
    psql(pg_bin, conn, "CREATE EXTENSION IF NOT EXISTS pg_trgm; CREATE EXTENSION IF NOT EXISTS citext;", db="gbcfms")
    print("  تم: المستخدم gbcfms وقاعدة البيانات gbcfms.")
    return {"password": password, "port": conn["port"]}


def main():
    if sys.version_info < (3, 11):
        fail(f"إصدار Python الحالي {sys.version.split()[0]}؛ المطلوب 3.11 أو أحدث من https://www.python.org/downloads/")

    step(1, "البحث عن PostgreSQL...")
    pg_bin = find_pg_bin()
    print(f"  {pg_bin}")

    step(2, "تجهيز بيئة Python وتثبيت المكتبات (قد يستغرق بضع دقائق في المرة الأولى)...")
    if not VPY.is_file():
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
    deps = tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    subprocess.run([str(VPY), "-m", "pip", "install", "--disable-pip-version-check", "-q", "--upgrade", "pip"], check=True)
    r = subprocess.run([str(VPY), "-m", "pip", "install", "--disable-pip-version-check", "-q", *deps])
    if r.returncode != 0:
        fail("فشل تثبيت المكتبات. تأكد من اتصال الإنترنت ثم أعد التشغيل.")

    step(3, "قاعدة البيانات والإعدادات...")
    env = read_env()
    if not env.get("GBCFMS_DATABASE_URL"):
        db = setup_database(pg_bin)
        env = {
            "GBCFMS_ENV": "prod",
            "GBCFMS_DATABASE_URL": f"postgresql+psycopg://gbcfms:{db['password']}@localhost:{db['port']}/gbcfms",
            "GBCFMS_JWT_SECRET": secrets.token_urlsafe(48),
            "GBCFMS_BACKUP_KEY": b64encode(secrets.token_bytes(32)).decode(),
            "GBCFMS_STORAGE_DIR": (ROOT / "data").as_posix(),
            "GBCFMS_WEB_DIR": WEB.as_posix(),
            "GBCFMS_COOKIE_SECURE": "false",
            "GBCFMS_PG_BIN": pg_bin.as_posix(),
            "GBCFMS_PORT": "8080",
        }
        lines = ["# أُنشئ بواسطة windows-local/setup_local.py — يحتوي أسرارًا: لا تشاركه، واحفظ نسخة منه خارج الجهاز"]
        lines += [f"{k}={v}" for k, v in env.items()]
        ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"  كُتب {ENV_FILE}")
    else:
        print("  الإعدادات موجودة مسبقًا (backend/.env)؛ لم تُغيَّر.")
    run_env = {**os.environ, "PATH": env["GBCFMS_PG_BIN"].replace("/", os.sep) + os.pathsep + os.environ.get("PATH", ""),
               "PYTHONIOENCODING": "utf-8"}

    step(4, "إنشاء جداول قاعدة البيانات أو تحديثها...")
    r = subprocess.run([str(VPY), "-m", "alembic", "upgrade", "head"], cwd=BACKEND, env=run_env)
    if r.returncode != 0:
        fail("فشل تحديث قاعدة البيانات. أرسل الرسائل الظاهرة أعلاه.")

    step(5, "المسؤول الأول (admin) والصلاحيات...")
    r = subprocess.run([str(VPY), "-c", "from sqlalchemy import select; from app.core.db import new_session;"
                        " from app.modules.users.models import User; s = new_session();"
                        " print(1 if s.scalar(select(User.id).where(User.username == 'admin')) else 0)"],
                       cwd=BACKEND, env=run_env, capture_output=True, text=True)
    if r.stdout.strip() == "1":
        subprocess.run([str(VPY), "-m", "app.cli", "bootstrap"], cwd=BACKEND, env=run_env, check=True)
        print("  المسؤول admin موجود مسبقًا.")
    else:
        print("  اختر كلمة مرور للمسؤول: 12 حرفًا على الأقل، فيها حروف وأرقام، ولا تحتوي كلمة admin.")
        for _ in range(3):
            pw = getpass.getpass("  كلمة المرور (لا تظهر أثناء الكتابة): ")
            if pw != getpass.getpass("  أعد كتابتها للتأكيد: "):
                print("  الكلمتان غير متطابقتين.")
                continue
            r = subprocess.run([str(VPY), "-m", "app.cli", "bootstrap"], cwd=BACKEND,
                               env={**run_env, "GBCFMS_ADMIN_PASSWORD": pw})
            if r.returncode == 0:
                break
        else:
            fail("تعذر إنشاء المسؤول.")

    step(6, "تحميل الباب الثاني وبنوده...")
    subprocess.run([str(VPY), "-m", "app.cli", "seed-reference"], cwd=BACKEND, env=run_env, check=True)

    if not (WEB / "index.html").is_file():
        fail(f"ملفات الواجهة غير موجودة في {WEB}.")
    print("\n" + "=" * 64)
    print(" اكتمل التثبيت. شغّل النظام بالنقر على start.cmd")
    print(f" ثم افتح: http://localhost:{env.get('GBCFMS_PORT', '8080')}   (اسم المستخدم: admin)")
    print("\n مهم: مفتاح تشفير النسخ الاحتياطية — احفظه خارج هذا الجهاز:")
    print(f"   {env['GBCFMS_BACKUP_KEY']}")
    print(" واحفظ نسخة من الملف backend\\.env في مكان آمن.")
    print("=" * 64)


if __name__ == "__main__":
    main()
