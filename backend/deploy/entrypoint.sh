#!/bin/sh
# نقطة دخول حاويات الخادم.
#   init-secrets : يولّد الأسرار مرة واحدة في volume مشترك (كلمة مرور القاعدة، سر JWT، مفتاح النسخ)
#   api          : ترحيل المخطط ثم تشغيل Uvicorn
#   scheduler    : النسخ اليومي وتقييم التنبيهات
#   migrate      : ترحيل المخطط فقط
#   backup-key   : عرض مفتاح تشفير النسخ الاحتياطية لحفظه خارج الخادم
#   غير ذلك      : أمر من gbcfms CLI (bootstrap، seed-reference، backup)
# القيم المضبوطة صراحة في البيئة تتقدم على الأسرار المولّدة.
set -e
SECRETS_DIR="${GBCFMS_SECRETS_DIR:-/secrets}"

gen() {  # gen <file> <python-expr>: يُنشئ الملف إن لم يوجد
  if [ ! -s "$SECRETS_DIR/$1" ]; then
    umask 077
    python -c "import secrets, base64; print($2, end='')" > "$SECRETS_DIR/$1"
    echo "init-secrets: وُلّد $1"
  fi
}

load() {  # يحمّل الأسرار المولّدة في متغيرات البيئة الفارغة
  if [ -z "$GBCFMS_DATABASE_URL" ] && [ -s "$SECRETS_DIR/postgres_password" ]; then
    GBCFMS_DATABASE_URL="postgresql+psycopg://gbcfms:$(cat "$SECRETS_DIR/postgres_password")@${GBCFMS_DB_HOST:-db}:5432/gbcfms"
    export GBCFMS_DATABASE_URL
  fi
  if [ -z "$GBCFMS_JWT_SECRET" ] && [ -s "$SECRETS_DIR/jwt_secret" ]; then
    GBCFMS_JWT_SECRET="$(cat "$SECRETS_DIR/jwt_secret")"; export GBCFMS_JWT_SECRET
  fi
  if [ -z "$GBCFMS_BACKUP_KEY" ] && [ -s "$SECRETS_DIR/backup_key" ]; then
    GBCFMS_BACKUP_KEY="$(cat "$SECRETS_DIR/backup_key")"; export GBCFMS_BACKUP_KEY
  fi
  # لا تُترك قيم فارغة: الإعدادات تستخدم افتراضياتها حينها
  [ -n "$GBCFMS_BACKUP_KEY" ] || unset GBCFMS_BACKUP_KEY
}

case "$1" in
  init-secrets)
    mkdir -p "$SECRETS_DIR"
    gen postgres_password "secrets.token_hex(24)"
    gen jwt_secret "secrets.token_urlsafe(48)"
    gen backup_key "base64.b64encode(secrets.token_bytes(32)).decode()"
    chmod 644 "$SECRETS_DIR/postgres_password"   # تقرؤه حاوية PostgreSQL بمستخدمها
    exit 0
    ;;
  backup-key)
    load
    echo "${GBCFMS_BACKUP_KEY:?لا يوجد مفتاح}"
    exit 0
    ;;
esac

load
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
