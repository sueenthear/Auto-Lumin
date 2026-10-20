@echo off
setlocal
set "APP_DIR=%~dp0"
set "APP_PYTHON=%~dp0.venv\Scripts\pythonw.exe"
if not exist "%APP_PYTHON%" (
    echo [ERROR] Project Python not found: "%APP_PYTHON%"
    pause
    exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath $env:APP_PYTHON -ArgumentList @('-m','dev_tool.audio_recorder') -WorkingDirectory $env:APP_DIR -Verb RunAs"
