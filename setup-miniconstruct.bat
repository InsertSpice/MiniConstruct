@echo off
setlocal EnableExtensions DisableDelayedExpansion

title MiniConstruct Setup

set "REPO_DIR=%~dp0"
if "%REPO_DIR:~-1%"=="\" set "REPO_DIR=%REPO_DIR:~0,-1%"
set "VENV_DIR=%REPO_DIR%\.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"

pushd "%REPO_DIR%" >nul 2>&1
if errorlevel 1 goto :fail

echo.
echo MiniConstruct Setup
echo ===================
echo.
echo [1/4] Checking Python...

set "PYTHON_CMD="
set "PYTHON_ARGS="

rem Prefer the Python launcher, but verify that the requested interpreter runs.
where py >nul 2>&1
if not errorlevel 1 (
    py -3.12 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_CMD=py"
        set "PYTHON_ARGS=-3.12"
    )
)

rem If a launcher is present without exactly 3.12, accept its newest supported
rem Python and retain the generic launcher argument for venv creation.
if not defined PYTHON_CMD (
    py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_CMD=py"
        set "PYTHON_ARGS=-3"
    )
)

rem Fall back to python, including when the launcher is not installed. Running
rem -c avoids accepting the Microsoft Store app-execution-alias stub.
if not defined PYTHON_CMD (
    where python >nul 2>&1
    if not errorlevel 1 (
        python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>&1
        if not errorlevel 1 (
            set "PYTHON_CMD=python"
        )
    )
)

if not defined PYTHON_CMD (
    echo          ERROR: Python 3.12 or newer was not found.
    echo          Install Python 3.12+ from https://www.python.org/downloads/windows/ and run setup again.
    goto :fail
)

echo          Interpreter: %PYTHON_CMD% %PYTHON_ARGS%
for /f "delims=" %%V in ('%PYTHON_CMD% %PYTHON_ARGS% -c "import sys; print(sys.version.split()[0])" 2^>nul') do echo          Python %%V

echo.
echo [2/4] Preparing virtual environment...
if exist "%VENV_DIR%\." (
    if not exist "%VENV_PY%" (
        echo          ERROR: .venv exists but .venv\Scripts\python.exe is missing.
        echo          It was not deleted or replaced; repair or remove it manually, then rerun setup.
        goto :fail
    )
    echo          Reusing .venv
) else (
    echo          Creating .venv
    %PYTHON_CMD% %PYTHON_ARGS% -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo          ERROR: virtual environment creation failed.
        goto :fail
    )
)

if not exist "%VENV_PY%" (
    echo          ERROR: .venv was not created correctly.
    goto :fail
)
"%VENV_PY%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>&1
if errorlevel 1 (
    echo          ERROR: existing .venv is not runnable Python 3.12+.
    echo          It was not deleted or replaced; repair or remove it manually, then rerun setup.
    goto :fail
)

echo.
echo [3/4] Installing MiniConstruct...
"%VENV_PY%" -m pip --version >nul 2>&1
if errorlevel 1 (
    echo          ERROR: pip is unavailable in .venv.
    goto :fail
)
"%VENV_PY%" -m pip install -e .
if errorlevel 1 (
    echo          ERROR: MiniConstruct installation failed.
    goto :fail
)
echo          Done

echo.
echo [4/4] Acquiring official MiniMax H3 guides...
"%VENV_PY%" -m miniconstruct.h3.guide_acquisition
if errorlevel 1 (
    echo          ERROR: official MiniMax H3 guide acquisition failed.
    goto :fail
)
"%VENV_PY%" -c "from miniconstruct.h3.guide_acquisition import require_guides; require_guides()"
if errorlevel 1 (
    echo          ERROR: required MiniMax H3 guides could not be verified.
    goto :fail
)
echo          Verified

echo.
echo Setup complete.
echo.
set "LAUNCH="
set /p "LAUNCH=Launch MiniConstruct now? [Y/n] "
if not defined LAUNCH set "LAUNCH=Y"
if /i "%LAUNCH%"=="Y" goto :launch
if /i "%LAUNCH%"=="YES" goto :launch

echo.
echo To launch MiniConstruct later, run:
echo   powershell -NoProfile -ExecutionPolicy Bypass -File "%REPO_DIR%\run.ps1"
popd
exit /b 0

:launch
echo.
echo Launching MiniConstruct...
powershell -NoProfile -ExecutionPolicy Bypass -File "%REPO_DIR%\run.ps1"
set "EXITCODE=%ERRORLEVEL%"
if not "%EXITCODE%"=="0" (
    echo.
    echo MiniConstruct launcher exited with error code %EXITCODE%.
)
popd
exit /b %EXITCODE%

:fail
set "EXITCODE=%ERRORLEVEL%"
if "%EXITCODE%"=="0" set "EXITCODE=1"
echo.
echo Setup failed. No existing .venv was deleted or replaced.
echo.
pause
popd >nul 2>&1
exit /b %EXITCODE%
