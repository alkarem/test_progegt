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
docker compose ps -q api 2>nul | findstr . >nul
if errorlevel 1 (
  echo النظام غير مثبت أو متوقف. شغّل install.cmd أولا، أو start.cmd إن كان مثبتا من قبل.
  pause
  exit /b 1
)
rem يعرض مفتاح تشفير النسخ الاحتياطية. احفظه خارج الجهاز (ورقة في مكان آمن أو مدير كلمات مرور).
echo مفتاح تشفير النسخ الاحتياطية:
echo.
docker compose exec -T api entrypoint.sh backup-key
echo.
echo احفظ هذا المفتاح خارج الجهاز. عند نقل النظام لجهاز جديد يوضع في ملف .env بالصيغة:
echo GBCFMS_BACKUP_KEY=المفتاح
pause
