@echo off
setlocal
set "ROOT=%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%ROOT%scripts\launcher\current.ps1" stop -KeepWindow
exit /b %ERRORLEVEL%
