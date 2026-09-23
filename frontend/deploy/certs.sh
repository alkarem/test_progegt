#!/bin/sh
# يُشغَّل تلقائيًا من صورة nginx قبل البدء (/docker-entrypoint.d).
# إن وُضعت شهادة الجهة في المجلد المركّب تُستخدم؛ وإلا تُولَّد شهادة موقعة ذاتيًا مرة واحدة وتُحفظ في volume.
set -e
SRC=/etc/nginx/certs-host
DST=/etc/nginx/certs
mkdir -p "$DST"
if [ -s "$SRC/server.crt" ] && [ -s "$SRC/server.key" ]; then
  cp "$SRC/server.crt" "$SRC/server.key" "$DST/"
  echo "certs: استخدام الشهادة المقدمة"
elif [ ! -s "$DST/server.crt" ]; then
  CN="${GBCFMS_HOST:-localhost}"
  openssl req -x509 -newkey rsa:3072 -nodes -days 825 \
    -keyout "$DST/server.key" -out "$DST/server.crt" \
    -subj "/CN=$CN" -addext "subjectAltName=DNS:$CN,DNS:localhost,IP:127.0.0.1" 2>/dev/null
  echo "certs: وُلِّدت شهادة موقعة ذاتيًا لـ $CN"
fi
chmod 600 "$DST/server.key"
