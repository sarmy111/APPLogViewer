@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
python -m unittest discover -s tests -v
if errorlevel 1 goto tests_failed
echo.
echo All tests passed.
exit /b 0

:tests_failed
echo.
echo Tests failed.
pause
exit /b 1
endlocal
