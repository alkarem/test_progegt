@echo off
chcp 65001 >nul
cd /d "%~dp0.."
docker compose up -d
if errorlevel 1 (echo تعذر التشغيل: تأكد أن Docker Desktop يعمل) else (start https://localhost)
pause
