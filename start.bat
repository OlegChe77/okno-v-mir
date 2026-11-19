@echo off
rem ASCII only: cmd breaks goto labels on UTF-8 text. Messages live in tools\say.ps1.
rem Normal start: no console, splash screen, server in background, tray icon (launcher.py).
rem Python order: 1) python\ next to the program, 2) installed in the system, 3) download the portable one.
cd /d "%~dp0"
set "PYW=%~dp0python\pythonw.exe"
if exist "%PYW%" goto run

python -c "import ssl, ctypes" >nul 2>nul
if errorlevel 1 goto download
where pythonw >nul 2>nul
if errorlevel 1 goto console
start "" pythonw "%~dp0launcher.py"
exit /b 0

:download
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\say.ps1" download
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\get_python.ps1"
if errorlevel 1 goto fail
if not exist "%PYW%" goto fail

:run
start "" "%PYW%" "%~dp0launcher.py"
exit /b 0

:console
rem no pythonw: run in a console window, as before
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\say.ps1" console
python server.py
pause
exit /b 0

:fail
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\say.ps1" fail
pause
exit /b 1
