@echo off
rem Gets the newest PhotoCull from GitHub, then refreshes the install. Needs Git for Windows.
cd /d "%~dp0"
where git >nul 2>nul
if errorlevel 1 goto :nogit
echo Getting the newest version...
git pull --ff-only
if errorlevel 1 goto :failed
call "%~dp0app\setup.bat"
exit /b %errorlevel%

:nogit
echo.
echo  PROBLEM: Git is not installed on this PC.
echo  FIX: install "Git for Windows" from https://git-scm.com/download/win and run this again.
pause
exit /b 1

:failed
echo.
echo  PROBLEM: could not get the update (no internet, or files were changed by hand here).
echo  Take a photo of this window and send it to the developer.
pause
exit /b 1
