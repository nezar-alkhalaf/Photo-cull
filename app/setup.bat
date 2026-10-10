@echo off
rem One-click install for a new PC: private Python environment + libraries + shortcut.
rem Safe to run again at any time. Works when the folder path has spaces or Arabic letters.
chcp 65001 >nul
setlocal EnableExtensions
set "APP=%~dp0"
set "ROOT=%APP%..\"
set "VENV=%APP%.venv"
set "VPY=%VENV%\Scripts\python.exe"
set "WARNINGS="
set "GLOG_minloglevel=2"

echo.
echo  ==============================================
echo    PhotoCull - first-time setup
echo    This takes a few minutes. Please keep this
echo    window open until it says Done.
echo  ==============================================
echo.

rem ---- 1. find Python 3.11 or newer (3.12 preferred: it is what PhotoCull was tested on)
set "PY="
for %%V in (3.12 3.11 3.13) do if not defined PY (
    py -%%V -c "import sys" >nul 2>nul && set "PY=py -%%V"
)
if not defined PY (
    python -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3, 11) else 1)" >nul 2>nul && set "PY=python"
)
if not defined PY goto :nopython
echo [1/6] Python found: %PY%

rem ---- 2. private environment inside app\.venv (rebuilt if it was copied from another PC)
rem (no ( ) blocks below: a folder name containing a bracket would break them)
if not exist "%VPY%" goto :mkvenv
"%VPY%" -c "import sys" >nul 2>nul
if not errorlevel 1 goto :venvok
echo       The old environment does not work on this PC, rebuilding it...
attrib -h "%VENV%" /d >nul 2>nul
rmdir /s /q "%VENV%"
:mkvenv
echo [2/6] Creating the environment...
%PY% -m venv "%VENV%"
if errorlevel 1 goto :novenv
goto :venvdone
:venvok
echo [2/6] Environment already there.
:venvdone

rem ---- 3. libraries, exact tested versions
echo [3/6] Installing libraries (needs internet, about 300 MB)...
"%VPY%" -m pip install --disable-pip-version-check -r "%APP%requirements.txt"
if errorlevel 1 goto :nopip

rem ---- 4. Photoshop link (pywin32)
"%VPY%" -c "import win32com.client, pythoncom" >nul 2>nul
if not errorlevel 1 goto :pswok
echo [4/6] Repairing the Photoshop link...
"%VPY%" "%VENV%\Scripts\pywin32_postinstall.py" -install >nul 2>nul
"%VPY%" -c "import win32com.client, pythoncom" >nul 2>nul
if errorlevel 1 set "WARNINGS=%WARNINGS% [Photoshop link not working: steps 1 and 3 still work]"
goto :pswdone
:pswok
echo [4/6] Photoshop link OK.
:pswdone

rem ---- 5. the PhotoCull shortcut (camera icon) in this folder and on the Desktop
echo [5/6] Creating the PhotoCull shortcut...
set "SC_ARGS="
if defined PHOTOCULL_NOSHORTCUT set "SC_ARGS=-NoDesktop"
powershell -NoProfile -ExecutionPolicy Bypass -File "%APP%shortcuts.ps1" %SC_ARGS% >nul 2>nul
if errorlevel 1 set "WARNINGS=%WARNINGS% [could not create the shortcut: use Start PhotoCull.bat in the folder]"
if not errorlevel 1 attrib +h "%ROOT%Start PhotoCull.bat" >nul 2>nul

rem ---- 6. hide the internals, then check everything
echo [6/6] Tidying the folder and checking this PC...
for /d /r "%APP%photocull" %%D in (__pycache__) do attrib +h "%%D" /d >nul 2>nul
attrib +h "%APP%tests" /d >nul 2>nul
attrib +h "%APP%.pytest_cache" /d >nul 2>nul
attrib +h "%VENV%" /d >nul 2>nul
set "APPDIR=%APP:~0,-1%"
attrib +h "%APPDIR%" /d >nul 2>nul

echo.
pushd "%APP%"
"%VPY%" -m photocull doctor
set "DOC=%errorlevel%"
popd
echo.
if not "%DOC%"=="0" goto :docfail
echo  ==============================================
echo    Done! Now double-click the PhotoCull icon
echo    in this folder (or on the Desktop).
echo  ==============================================
if defined WARNINGS echo    Notes: %WARNINGS%
call :wait
exit /b 0

:nopython
echo.
echo  PROBLEM: Python 3.11 or newer was not found on this PC.
echo  FIX: install Python 3.12 from https://www.python.org/downloads/
echo       and tick "Add Python to PATH" in the first screen. Then run this setup again.
call :wait
exit /b 1

:novenv
echo.
echo  PROBLEM: could not create the Python environment.
echo  FIX: move the PhotoCull folder to your Desktop or Documents, then run this setup again.
call :wait
exit /b 1

:nopip
echo.
echo  PROBLEM: installing the libraries failed (see the red text above).
echo  FIX: check the internet connection and run this setup again. If it still fails,
echo       take a photo of this window and send it to the developer.
call :wait
exit /b 1

:docfail
echo  ==============================================
echo    Setup finished, but the check found problems.
echo    Read the [FAIL] lines above for how to fix them.
echo  ==============================================
call :wait
exit /b 1

:wait
if not defined PHOTOCULL_NOPAUSE pause
exit /b 0
