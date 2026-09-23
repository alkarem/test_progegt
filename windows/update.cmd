@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo نسخة احتياطية قبل التحديث...
docker compose run --rm api backup || (echo فشل النسخ الاحتياطي؛ أُلغي التحديث & pause & exit /b 1)
git pull
docker compose build && docker compose run --rm api migrate && docker compose up -d
pause
