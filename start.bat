@echo off
rem ==========================================================================
rem  gemini-web2api - one-click Windows launcher
rem
rem  Double-click this file. It will:
rem    1. find a Python 3.8+ interpreter
rem    2. create a virtual environment in .venv (first run only)
rem    3. install dependencies from requirements.txt
rem    4. write a safe default config.json (first run only)
rem    5. start the server and open the dashboard in your browser
rem
rem  Press Ctrl+C in this window to stop the server.
rem
rem  Any arguments are passed through to the server, e.g.
rem      start.bat --port 9000 --api-key sk-my-secret
rem ==========================================================================
setlocal EnableExtensions
cd /d "%~dp0"

echo.
echo   gemini-web2api - setup and launch
echo   ---------------------------------
echo.

rem --------------------------------------------------------------------------
rem 1. Find a usable Python 3.8+
rem
rem    The Microsoft Store ships a "python.exe" stub that opens the Store
rem    instead of running Python, so every candidate is verified by actually
rem    executing it rather than by checking whether it is on PATH.
rem --------------------------------------------------------------------------
set "PY="
for %%C in ("py -3" "python" "python3") do (
    if not defined PY (
        %%~C -c "import sys;sys.exit(0 if sys.version_info>=(3,8) else 1)" >nul 2>&1
        if not errorlevel 1 set "PY=%%~C"
    )
)

if not defined PY (
    echo   [X] No usable Python 3.8 or newer was found.
    echo.
    echo       Install Python from https://www.python.org/downloads/windows/
    echo       and tick "Add python.exe to PATH" during setup, then run this
    echo       file again.
    echo.
    echo       If Python is already installed, an older version may be first on
    echo       PATH. Check with:  py -0
    echo.
    goto :fail
)

for /f "delims=" %%V in ('%PY% -c "import sys;print('.'.join(map(str,sys.version_info[:3])))"') do set "PYVER=%%V"
echo   [1/5] Found Python %PYVER%

rem --------------------------------------------------------------------------
rem 2. Virtual environment
rem --------------------------------------------------------------------------
set "VENV=%~dp0.venv"
set "VPY=%VENV%\Scripts\python.exe"

if exist "%VPY%" (
    "%VPY%" -c "import sys" >nul 2>&1
    if errorlevel 1 (
        echo         existing .venv is broken - recreating it
        rmdir /s /q "%VENV%" >nul 2>&1
    )
)

if not exist "%VPY%" (
    echo   [2/5] Creating virtual environment in .venv ...
    %PY% -m venv "%VENV%"
    if errorlevel 1 (
        echo   [X] Could not create a virtual environment.
        echo       If the venv module is missing, reinstall Python and enable
        echo       the "tcl/tk and pip" optional features.
        goto :fail
    )
) else (
    echo   [2/5] Using existing virtual environment
)

rem --------------------------------------------------------------------------
rem 3. Dependencies
rem
rem    httpx is optional but strongly recommended: without it, stream:true
rem    returns one buffered chunk instead of a real stream. A failure here is
rem    therefore a warning, not a fatal error - the server runs regardless.
rem --------------------------------------------------------------------------
echo   [3/5] Installing dependencies ...
"%VPY%" -m pip install --quiet --disable-pip-version-check --upgrade pip >nul 2>&1
"%VPY%" -m pip install --quiet --disable-pip-version-check -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo         WARNING: dependency installation failed.
    echo         The server will still start, but streaming will be buffered.
    echo         Check your network connection or proxy settings and re-run.
) else (
    echo         dependencies installed
)

rem --------------------------------------------------------------------------
rem 4. Configuration
rem --------------------------------------------------------------------------
echo   [4/5] Preparing configuration ...
set "PORT=8081"
set "CREATED=0"
for /f "usebackq tokens=1,* delims==" %%A in (`"%VPY%" "%~dp0scripts\win_setup.py"`) do (
    if /I "%%A"=="PORT" set "PORT=%%B"
    if /I "%%A"=="CREATED" set "CREATED=%%B"
)

rem An explicit --port on the command line wins over config.json.
set "ARGPORT="
:scanargs
if "%~1"=="" goto :argsdone
if /I "%~1"=="--port" set "ARGPORT=%~2"
shift
goto :scanargs
:argsdone
if defined ARGPORT set "PORT=%ARGPORT%"

rem --------------------------------------------------------------------------
rem 5. Launch
rem --------------------------------------------------------------------------
echo   [5/5] Starting server ...
echo.
echo   ======================================================================
echo.
echo     Dashboard   http://localhost:%PORT%/
echo     API base    http://localhost:%PORT%/v1
echo     Health      http://localhost:%PORT%/health
echo.
if "%CREATED%"=="1" (
    echo     A new config.json was created, bound to 127.0.0.1 so that only
    echo     this computer can reach the server. Authentication is off.
    echo.
    echo     To use it from another machine, edit config.json and set:
    echo         "host": "0.0.0.0"     and add a key to "api_keys"
    echo     See docs\SECURITY.md before exposing it to a network.
    echo.
)
echo     Point any OpenAI client at the API base URL above.
echo     Press Ctrl+C in this window to stop the server.
echo.
echo   ======================================================================
echo.

rem Open the browser once the server has had a moment to bind. Delays matter
rem here: launching the browser immediately races the socket bind and shows a
rem "can't connect" page on slower machines. PowerShell is used because nested
rem quoting makes a delayed `start` unreliable in batch.
start "" /min powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 4; Start-Process 'http://localhost:%PORT%/'" >nul 2>&1

"%VPY%" -m gemini_web2api --port %PORT% %*

echo.
echo   Server stopped.
goto :end

:fail
echo.
setlocal EnableDelayedExpansion
echo   Setup did not complete. The message above explains why.
echo.
pause
exit /b 1

:end
echo.
pause
endlocal
