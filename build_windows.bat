@echo off
rem ==========================================================================
rem  gemini-web2api - build the Windows application
rem
rem  Double-click this file, or run it from a terminal:
rem      build_windows.bat               onedir build + portable zip
rem      build_windows.bat --onefile     single gemini-web2api.exe
rem      build_windows.bat --clean       remove the previous build first
rem      build_windows.bat --dry-run     print the plan, build nothing
rem
rem  Output: dist\windows\gemini-web2api\gemini-web2api.exe
rem          dist\windows\gemini-web2api-VERSION-windows-x64.zip
rem  Requires Python 3.8+ on PATH (python.org installer). Nothing is installed
rem  outside .venv-build.
rem ==========================================================================
setlocal EnableExtensions
cd /d "%~dp0"

set "PY="
for %%C in ("py -3" "python" "python3") do (
    if not defined PY (
        %%~C -c "import sys;sys.exit(0 if sys.version_info>=(3,8) else 1)" >nul 2>&1
        if not errorlevel 1 set "PY=%%~C"
    )
)

if not defined PY (
    echo   [X] No usable Python 3.8 or newer was found.
    echo       Install it from https://www.python.org/downloads/windows/ and tick
    echo       "Add python.exe to PATH", then run this file again.
    goto :fail
)

echo.
echo   gemini-web2api - Windows build
echo   ------------------------------
echo.
%PY% "%~dp0scripts\build_windows.py" %*
if errorlevel 1 goto :fail

echo.
echo   Done. The executable and archive are listed above, under dist\windows\.
goto :end

:fail
echo.
echo   Build did not complete. The message above explains why.
set "RC=1"
goto :finish

:end
set "RC=0"

:finish
echo.
if "%~1"=="" pause
endlocal & exit /b %RC%
