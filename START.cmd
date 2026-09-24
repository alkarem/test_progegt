@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem تشغيل النظام بالطريقة التي ثُبّت بها.
if exist "backend\.venv\Scripts\python.exe" if exist "backend\.env" (
  call "windows-local\start.cmd"
  exit /b
)
call "windows\start.cmd"
