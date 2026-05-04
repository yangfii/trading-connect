@echo off
title Gold Performance Dashboard - Yang Fi
color 0A

echo ================================================
echo   GOLD PERFORMANCE DASHBOARD
echo   Yang Fi - Gold Trader
echo ================================================
echo.

:: Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found!
    echo Please install Python from https://python.org
    pause
    exit /b
)

:: Install Flask if not installed
echo Checking Flask...
python -c "import flask" >nul 2>&1
if errorlevel 1 (
    echo Installing Flask...
    pip install flask --quiet
)

:: Change to the script directory
cd /d "%~dp0"

echo.
echo Starting server...
echo Dashboard: http://localhost:5000
echo.
echo Opening browser in 3 seconds...
timeout /t 3 /nobreak >nul

:: Open browser
start http://localhost:5000

:: Start the server
python dashboard_server.py

pause
