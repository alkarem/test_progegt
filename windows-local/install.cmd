@echo off
chcp 65001 >nul
cd /d "%~dp0.."
title تثبيت نظام مراقبة الاعتمادات
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1 && set "PY=py -3"
if not defined PY python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1 && set "PY=python"
if not defined PY (
  echo لم يُعثر على Python 3.11 أو أحدث.
  echo ثبّته من https://www.python.org/downloads/ مع تفعيل خيار Add python.exe to PATH ثم أعد المحاولة.
  pause
  exit /b 1
)
%PY% windows-local\setup_local.py
pause
