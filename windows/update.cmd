@echo off
chcp 65001 >nul
cd /d "%~dp0.."
docker info >nul 2>&1
if errorlevel 1 (
  echo.
  echo Docker Desktop لا يعمل الآن.
  echo افتحه من قائمة ابدأ وانتظر حتى يظهر في أسفله Engine running ثم أعد تشغيل هذا الملف.
  echo إن لم يكن مثبتا: https://www.docker.com/products/docker-desktop/
  echo.
  pause
  exit /b 1
)
echo نسخة احتياطية قبل التحديث...
docker compose run --rm api backup || (echo فشل النسخ الاحتياطي؛ أُلغي التحديث & pause & exit /b 1)
git pull
docker compose build && docker compose run --rm api migrate && docker compose up -d
pause
