@echo off
cd /d "%~dp0.."
set "PYTHONW="
for /f "delims=" %%P in ('py -3 -c "import sys; print(sys.executable.replace('python.exe', 'pythonw.exe'))" 2^>nul') do set "PYTHONW=%%P"
if not defined PYTHONW set "PYTHONW=pythonw"
start "" "%PYTHONW%" -m dev_tool.ok_lumin_tool
