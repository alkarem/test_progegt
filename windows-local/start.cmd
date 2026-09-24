@echo off
chcp 65001 >nul
cd /d "%~dp0.."
title نظام مراقبة الاعتمادات - لا تغلق هذه النافذة أثناء العمل
if not exist "backend\.venv\Scripts\python.exe" (
  echo النظام غير مثبت بعد. شغّل install.cmd أولا.
  pause
  exit /b 1
)
"backend\.venv\Scripts\python.exe" windows-local\run_local.py serve
pause
