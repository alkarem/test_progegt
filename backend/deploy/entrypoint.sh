#!/bin/sh
# api: ترحيل المخطط ثم تشغيل Uvicorn. scheduler: النسخ اليومي وتقييم التنبيهات. غير ذلك: أمر gbcfms CLI.
set -e
case "$1" in
  api)
    alembic upgrade head
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers "${GBCFMS_WORKERS:-4}" \
         --proxy-headers --forwarded-allow-ips="*" --no-server-header
    ;;
  scheduler)
    exec python -m app.cli scheduler
    ;;
  migrate)
    exec alembic upgrade head
    ;;
  *)
    exec python -m app.cli "$@"
    ;;
esac
