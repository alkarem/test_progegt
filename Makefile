# أوامر التشغيل (15-deployment). تتطلب Docker Compose v2 وملف .env (انسخ deploy/env.example).
COMPOSE ?= docker compose
.DEFAULT_GOAL := help

help:  ## عرض الأوامر
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-10s %s\n", $$1, $$2}'

init:  ## تجهيز مجلد النسخ الاحتياطية (الأسرار تُولَّد تلقائيًا عند أول تشغيل)
	@mkdir -p backups && (chown 10001 backups 2>/dev/null || echo "نفّذ: sudo chown 10001 backups")
	@test -f .env || echo "ملف .env اختياري (المنافذ، المساعد الذكي): cp deploy/env.example .env"

backup-key:  ## عرض مفتاح تشفير النسخ الاحتياطية لحفظه خارج الخادم
	$(COMPOSE) exec -T api entrypoint.sh backup-key

certs:  ## شهادة موقعة ذاتيًا على المضيف (اختياري؛ الحاوية تولّد واحدة تلقائيًا إن لم توجد)
	@mkdir -p deploy/certs
	openssl req -x509 -newkey rsa:3072 -nodes -days 825 -keyout deploy/certs/server.key -out deploy/certs/server.crt \
	  -subj "/CN=$${GBCFMS_HOST:-gbcfms.local}" -addext "subjectAltName=DNS:$${GBCFMS_HOST:-gbcfms.local},DNS:localhost"

build:  ## بناء الصور
	$(COMPOSE) build

up:  ## تشغيل النظام (يرحّل المخطط تلقائيًا)
	$(COMPOSE) up -d
	$(COMPOSE) ps

bootstrap:  ## إنشاء المسؤول الأول ومزامنة الصلاحيات وتحميل الباب الثاني وبنوده
	$(COMPOSE) run --rm api bootstrap
	$(COMPOSE) run --rm api seed-reference

backup:  ## نسخة احتياطية فورية مع التحقق
	$(COMPOSE) run --rm api backup

upgrade:  ## ترقية: نسخة احتياطية ← بناء ← ترحيل ← إعادة تشغيل (التراجع بالاستعادة من الإعدادات)
	$(COMPOSE) run --rm api backup
	$(COMPOSE) build
	$(COMPOSE) run --rm api migrate
	$(COMPOSE) up -d

logs:  ## متابعة السجلات
	$(COMPOSE) logs -f --tail=200

down:  ## إيقاف (البيانات تبقى في المجلدات والـ volumes)
	$(COMPOSE) down

test:  ## اختبارات الخادم والواجهة محليًا (تتطلب PostgreSQL 16 للاختبارات)
	cd backend && .venv/bin/ruff check app tests && .venv/bin/pytest -n auto
	cd frontend && npm run typecheck && npm test && npm run build

.PHONY: help init backup-key certs build up bootstrap backup upgrade logs down test
