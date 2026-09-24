@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem نقطة البدء الوحيدة للتثبيت على Windows: Docker إن كان يعمل، وإلا التثبيت المباشر (PostgreSQL وPython).
docker info >nul 2>&1
if not errorlevel 1 (
  echo Docker Desktop يعمل: سيُستخدم التثبيت عبر Docker.
  call "windows\install.cmd"
  exit /b
)
echo التثبيت المباشر على الجهاز (PostgreSQL وPython).
call "windows-local\install.cmd"
