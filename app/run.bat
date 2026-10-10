@echo off
rem Launches PhotoCull without a console window. Used by the PhotoCull shortcut.
setlocal
set "APP=%~dp0"
set "PYW=%APP%.venv\Scripts\pythonw.exe"
if not exist "%PYW%" goto :notsetup
"%APP%.venv\Scripts\python.exe" -c "import sys" >nul 2>nul
if errorlevel 1 goto :notsetup
cd /d "%APP%"
start "" "%PYW%" -m photocull.launch
exit /b 0

:notsetup
powershell -NoProfile -WindowStyle Hidden -Command "Add-Type -AssemblyName PresentationFramework; [void][System.Windows.MessageBox]::Show('PhotoCull is not set up on this PC yet.' + [Environment]::NewLine + [Environment]::NewLine + 'Double-click First-Time Setup once, wait until it says Done, then open PhotoCull again.', 'PhotoCull', 'OK', 'Information')"
exit /b 1
