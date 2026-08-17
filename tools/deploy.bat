@echo off
rem ============================================================================
rem  cpumon - build the deployable archive
rem
rem  Runs every quality gate first and refuses to produce an artifact if any of
rem  them fails, so dist\cpumon.pyz is never a build of broken code.
rem
rem  Usage:  tools\deploy.bat        (from anywhere: it locates the project)
rem  Result: dist\cpumon.pyz  ->  copy to the device, run `python cpumon.pyz`
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

echo [3/5] mypy
"%PY%" -m mypy src tools
if errorlevel 1 goto :failed

echo [4/5] pytest
"%PY%" -m pytest -q
if errorlevel 1 goto :failed

echo [5/5] build
"%PY%" tools\build_zipapp.py
if errorlevel 1 goto :failed

echo.
echo ============================================================
echo  OK - dist\cpumon.pyz is ready
echo  copy it to the device and run: python cpumon.pyz
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
