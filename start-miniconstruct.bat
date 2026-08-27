@echo off
setlocal
cd /d "%~dp0"

title MiniConstruct

echo.
echo ========================================
echo   MiniConstruct
echo ========================================
echo.
echo Starting server at http://127.0.0.1:8743
echo Press Ctrl+C to stop MiniConstruct.
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] MiniConstruct's Python virtual environment was not found.
    echo.
    echo Expected:
    echo   .venv\Scripts\python.exe
    echo.
    echo Please follow the setup instructions in README.md first.
    echo.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -m miniconstruct --host 127.0.0.1 --port 8743

set "EXITCODE=%ERRORLEVEL%"

echo.
if not "%EXITCODE%"=="0" (
    echo MiniConstruct exited with error code %EXITCODE%.
) else (
    echo MiniConstruct has stopped.
)

echo.
pause
exit /b %EXITCODE%
