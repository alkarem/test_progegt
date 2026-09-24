"""تشغيل النظام المثبت مباشرة على Windows: الخادم والمجدول معًا، والإيقاف بإغلاق النافذة.

  run_local.py serve   تحديث المخطط ثم تشغيل الخادم والمجدول وفتح المتصفح
  run_local.py backup  نسخة احتياطية فورية
  run_local.py key     عرض مفتاح تشفير النسخ الاحتياطية
"""
import os
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass


def load_env() -> dict:
    f = BACKEND / ".env"
    if not f.is_file():
        print("النظام غير مثبت بعد. شغّل install.cmd أولًا.")
        sys.exit(1)
    env = {}
    for line in f.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def child_env(env: dict) -> dict:
    pg_bin = env.get("GBCFMS_PG_BIN", "").replace("/", os.sep)
    return {**os.environ, "PATH": pg_bin + os.pathsep + os.environ.get("PATH", ""), "PYTHONIOENCODING": "utf-8"}


def serve(env: dict) -> None:
    port = env.get("GBCFMS_PORT", "8080")
    cenv = child_env(env)
    py = sys.executable
    if subprocess.run([py, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, env=cenv).returncode != 0:
        print("فشل تحديث قاعدة البيانات. تأكد أن خدمة PostgreSQL تعمل.")
        sys.exit(1)
    scheduler = subprocess.Popen([py, "-m", "app.cli", "scheduler"], cwd=BACKEND, env=cenv)
    server = subprocess.Popen([py, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", port,
                               "--no-server-header", "--log-level", "warning"], cwd=BACKEND, env=cenv)
    url = f"http://localhost:{port}"

    def open_when_ready():
        for _ in range(60):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/health", timeout=2)   # noqa: S310
                print(f"\nالنظام يعمل: {url}\nمن أجهزة الشبكة: http://<اسم هذا الجهاز أو عنوانه>:{port}")
                print("لإيقاف النظام أغلق هذه النافذة أو اضغط Ctrl+C.\n")
                webbrowser.open(url)
                return
            except OSError:
                time.sleep(1)
        print("الخادم لم يبدأ خلال دقيقة؛ راجع الرسائل أعلاه.")

    threading.Thread(target=open_when_ready, daemon=True).start()
    try:
        while server.poll() is None:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for p in (server, scheduler):
            if p.poll() is None:
                p.terminate()
        print("\nتم إيقاف النظام.")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "serve"
    env = load_env()
    if cmd == "serve":
        serve(env)
    elif cmd == "backup":
        sys.exit(subprocess.run([sys.executable, "-m", "app.cli", "backup"], cwd=BACKEND, env=child_env(env)).returncode)
    elif cmd == "key":
        print(env.get("GBCFMS_BACKUP_KEY", "لا يوجد مفتاح"))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
