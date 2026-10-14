@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
set "PYTHONUTF8=1"
cd /d "%~dp0"
if errorlevel 1 exit /b 1
set "CARRIER_PY="
set "CARRIER_CHECK=import sys; assert sys.version_info >= (3,11) and sys.maxsize > 2**32"
rem Never ask py.exe for a specific version: the Python install manager (py install)
rem reports a missing version on the console and may stop this script.
rem "call" also keeps control here if py/python is a .bat shim (pyenv-win and similar).
call py -c "%CARRIER_CHECK%" >nul 2>&1
if not errorlevel 1 (
    set "CARRIER_PY=py"
    goto run
)
call python -c "%CARRIER_CHECK%" >nul 2>&1
if not errorlevel 1 (
    set "CARRIER_PY=python"
    goto run
)
for /d %%D in ("%LOCALAPPDATA%\Python\pythoncore-3*" "%LOCALAPPDATA%\Programs\Python\Python3*" "%ProgramFiles%\Python3*") do (
    if exist "%%~D\python.exe" (
        "%%~D\python.exe" -c "%CARRIER_CHECK%" >nul 2>&1
        if not errorlevel 1 (
            set "CARRIER_PY="%%~D\python.exe""
            goto run
        )
    )
)
echo Python 3.11 or newer, x64, was not found. Install it: https://www.python.org/downloads/windows/
set "CARRIER_STATUS=1"
goto finish
:run
%CARRIER_PY% -u launch.py %*
set "CARRIER_STATUS=%ERRORLEVEL%"
:finish
if not "%CARRIER_STATUS%"=="0" if "%~1"=="" pause
exit /b %CARRIER_STATUS%
