@echo off
chcp 65001 >nul
cd /d "%~dp0.."
docker compose logs --tail=200 -f
