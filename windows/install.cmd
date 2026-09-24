@echo off
chcp 65001 >nul
cd /d "%~dp0.."
rem تثبيت وتشغيل «نظام مراقبة الاعتمادات والمصروفات الحكومية». المتطلب الوحيد: Docker Desktop يعمل.
title تثبيت نظام مراقبة الاعتمادات

echo.
echo [1/4] التحقق من Docker Desktop...
docker info >nul 2>&1
if errorlevel 1 (
  echo Docker Desktop غير مثبت أو لا يعمل على هذا الجهاز.
  echo سيُستخدم التثبيت المباشر بدلا منه (يتطلب PostgreSQL وPython مثبتين^).
  echo.
  call "%~dp0..\windows-local\install.cmd"
  exit /b
)
if not exist backups mkdir backups

echo.
echo [2/4] بناء النظام وتشغيله. المرة الأولى قد تستغرق 5 إلى 15 دقيقة...
docker compose up -d --build
if errorlevel 1 (
  echo فشل البناء أو التشغيل. تأكد من اتصال الإنترنت ثم أعد المحاولة.
  pause
  exit /b 1
)

echo.
echo [3/4] انتظار جاهزية الخادم...
set /a tries=0
:wait
docker compose exec -T api python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health')" >nul 2>&1
if not errorlevel 1 goto ready
set /a tries+=1
if %tries% geq 60 (
  echo الخادم لم يصبح جاهزا خلال 5 دقائق. شغّل logs.cmd وأرسل الرسائل الظاهرة.
  pause
  exit /b 1
)
timeout /t 5 /nobreak >nul
goto wait
:ready

echo.
echo [4/4] إعداد المسؤول الأول admin والبيانات المرجعية...
echo كلمة المرور: 12 حرفا على الأقل، فيها حروف وأرقام، ولا تحتوي كلمة admin.
set /a tries=0
:admin
docker compose exec api entrypoint.sh bootstrap
if not errorlevel 1 goto seeded
set /a tries+=1
if %tries% geq 3 (
  echo تعذر إنشاء المسؤول.
  pause
  exit /b 1
)
echo كلمة المرور لا تستوفي الشروط، حاول مجددا.
goto admin
:seeded
docker compose exec -T api entrypoint.sh seed-reference

echo.
echo ============================================================
echo  اكتمل التثبيت. افتح: https://localhost
echo  اسم المستخدم: admin  (سيطلب تغيير كلمة المرور عند أول دخول)
echo  المتصفح سيحذر من الشهادة: اختر "متقدم" ثم "المتابعة".
echo.
echo  مهم: شغّل backup-key.cmd واحفظ المفتاح الظاهر في مكان آمن
echo  خارج هذا الجهاز؛ بدونه لا يمكن استعادة النسخ الاحتياطية.
echo ============================================================
start "" https://localhost
pause
