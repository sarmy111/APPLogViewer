@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
title Unity USB Log Viewer

where python >nul 2>nul
if errorlevel 1 goto no_python

echo Starting Unity USB Log Viewer...
echo Python:
where python
python -u app.py
if errorlevel 1 goto app_error
exit /b 0

:no_python
echo Python 3.11 was not found. Install Python and add it to PATH.
pause
exit /b 1

:app_error
echo.
echo The application exited with an error. See the output above.
pause
exit /b 1
endlocal
