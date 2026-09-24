@echo off
chcp 65001 >nul
cd /d "%~dp0.."
if not exist "backend\.venv\Scripts\python.exe" (
  echo النظام غير مثبت بعد. شغّل install.cmd أولا.
  pause
  exit /b 1
)
echo مفتاح تشفير النسخ الاحتياطية (احفظه خارج الجهاز):
echo.
"backend\.venv\Scripts\python.exe" windows-local\run_local.py key
echo.
pause
