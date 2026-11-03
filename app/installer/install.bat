@echo off
rem Runs from PhotoCull-Setup.exe: unpack PhotoCull into the user's folder, set it up, open it.
chcp 65001 >nul
setlocal EnableExtensions
if not defined PHOTOCULL_DEST set "PHOTOCULL_DEST=%LOCALAPPDATA%\PhotoCull"
set "DEST=%PHOTOCULL_DEST%"

echo.
echo  ==============================================
echo    Installing PhotoCull
echo    Please keep this window open. It takes a few
echo    minutes and needs internet the first time.
echo  ==============================================
echo.
if not exist "%DEST%" mkdir "%DEST%"
tar -xf "%~dp0payload.zip" -C "%DEST%"
if errorlevel 1 goto :failed

call "%DEST%\app\setup.bat"
if errorlevel 1 goto :end
if defined PHOTOCULL_NOLAUNCH goto :end
start "" "%DEST%\Start PhotoCull.bat"
goto :end

:failed
echo.
echo  PROBLEM: could not unpack PhotoCull. Make sure there is free disk space and try again.
if not defined PHOTOCULL_NOPAUSE pause
exit /b 1

:end
exit /b 0
