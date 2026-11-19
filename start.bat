@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Обычный запуск — без консоли: заставка «Загрузка…», сервер в фоне, значок в трее (launcher.py).
where pythonw >nul 2>nul
if errorlevel 1 goto console
start "" pythonw "%~dp0launcher.py"
exit /b 0

:console
rem pythonw не найден — запуск в консоли, как раньше
title Окно в мир
echo.
echo   ЗАГРУЗКА... Окно в мир запускается. Браузер откроется сам.
echo   Не закрывайте это окно, пока смотрите.
echo.
python server.py
pause
