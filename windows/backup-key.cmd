@echo off
chcp 65001 >nul
cd /d "%~dp0.."
rem يعرض مفتاح تشفير النسخ الاحتياطية. احفظه خارج الجهاز (ورقة في مكان آمن أو مدير كلمات مرور).
echo مفتاح تشفير النسخ الاحتياطية:
echo.
docker compose exec -T api entrypoint.sh backup-key
echo.
echo احفظ هذا المفتاح خارج الجهاز. عند نقل النظام لجهاز جديد يوضع في ملف .env بالصيغة:
echo GBCFMS_BACKUP_KEY=المفتاح
pause
