@echo off
setlocal

for %%I in ("%~dp0..") do set "PROJECT_ROOT=%%~fI"
set "UV_PROJECT_ENVIRONMENT=%PROJECT_ROOT%\.venv"
set "PUSHD_DONE="

pushd "%PROJECT_ROOT%" || goto :fail
set "PUSHD_DONE=1"
if not exist "pyproject.toml" (
    echo [ERROR] Project file not found: "%PROJECT_ROOT%\pyproject.toml"
    goto :fail
)

where uv >nul 2>&1
if errorlevel 1 (
    echo [ERROR] uv was not found. Install uv and make sure it is available on PATH.
    goto :fail
)

uv sync --locked
if errorlevel 1 (
    echo [ERROR] uv sync failed. The development tool was not started.
    goto :fail
)

set "VENV_ACTIVATE=%PROJECT_ROOT%\.venv\Scripts\activate.bat"
if not exist "%VENV_ACTIVATE%" (
    echo [ERROR] Virtual environment activation script not found: "%VENV_ACTIVATE%"
    goto :fail
)
call "%VENV_ACTIVATE%"
if errorlevel 1 (
    echo [ERROR] Failed to activate the project virtual environment.
    goto :fail
)

set "PYTHONW=%VIRTUAL_ENV%\Scripts\pythonw.exe"
if not exist "%PYTHONW%" (
    echo [ERROR] pythonw.exe was not found in the activated environment: "%PYTHONW%"
    goto :fail
)

start "" "%PYTHONW%" -m dev_tool.ok_lumin_tool
set "RESULT=%ERRORLEVEL%"
popd
exit /b %RESULT%

:fail
if defined PUSHD_DONE popd
echo [ERROR] Startup failed.
pause
exit /b 1
