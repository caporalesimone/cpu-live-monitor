@echo off
rem ============================================================================
rem  cpumon - build the deployable archive
rem
rem  Runs every quality gate first and refuses to produce an artifact if any of
rem  them fails, so the archive is never a build of broken code.
rem
rem  Usage:  tools\deploy.bat        (from anywhere: it locates the project)
rem  Result: dist\cpumon-<version>.pyz  ->  copy to the device and run it
rem
rem  The same four gates run in CI (.github/workflows/ci.yml), and again when a
rem  v<x.y.z> tag drafts a release (.github/workflows/release.yml).
rem ============================================================================

setlocal
rem The project root is this script's parent, whatever the caller's directory is.
cd /d "%~dp0.."

rem Box drawing must survive a redirected stdout on Windows.
set "PYTHONIOENCODING=utf-8"

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo [warn] .venv not found, falling back to the python on PATH
    set "PY=python"
)

"%PY%" --version
if errorlevel 1 goto :no_python
echo.

echo [1/5] ruff check
"%PY%" -m ruff check .
if errorlevel 1 goto :failed

echo [2/5] ruff format --check
"%PY%" -m ruff format --check .
if errorlevel 1 goto :failed

rem Both platforms, from either one: the backend for the OS you are not sitting
rem at is the one that drifts unnoticed.
echo [3/5] mypy --platform win32
"%PY%" -m mypy --platform win32 src tools
if errorlevel 1 goto :failed

echo       mypy --platform linux
"%PY%" -m mypy --platform linux src tools
if errorlevel 1 goto :failed

echo [4/5] pytest
"%PY%" -m pytest -q
if errorlevel 1 goto :failed

echo [5/5] build
"%PY%" tools\build_zipapp.py
if errorlevel 1 goto :failed

rem The archive is named after [project].version. Ask the build script where it
rem put it rather than spelling that rule out a second time.
for /f "usebackq delims=" %%o in (`"%PY%" tools\build_zipapp.py --print-output`) do set "ARCHIVE=%%o"
for %%f in ("%ARCHIVE%") do set "ARCHIVE_NAME=%%~nxf"

echo.
echo ============================================================
echo  OK - %ARCHIVE% is ready
echo  copy it to the device and run: python %ARCHIVE_NAME%
echo ============================================================
endlocal
exit /b 0

:no_python
echo.
echo [FAIL] no usable Python interpreter found
endlocal
exit /b 1

:failed
echo.
echo ============================================================
echo  FAILED - no artifact was built
echo ============================================================
endlocal
exit /b 1
