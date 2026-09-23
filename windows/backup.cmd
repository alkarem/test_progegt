@echo off
chcp 65001 >nul
cd /d "%~dp0.."
docker compose run --rm api backup
echo النسخ في مجلد backups
pause
