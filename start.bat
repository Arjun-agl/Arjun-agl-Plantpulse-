@echo off
setlocal
cd /d "%~dp0"

set "VENV_PY=%~dp0venv\Scripts\python.exe"

REM -- 1. Check the venv actually works (a copied venv from another PC won't) --
set "VENV_OK="
if exist "%VENV_PY%" (
  "%VENV_PY%" -c "import sys" >nul 2>&1
  if not errorlevel 1 set "VENV_OK=1"
)

REM -- 2. Create a fresh venv if missing or broken --
if not defined VENV_OK (
  echo Setting up Python environment - first run only, please wait...
  if exist "%~dp0venv" rmdir /s /q "%~dp0venv"
  where py >nul 2>&1
  if not errorlevel 1 (
    py -3 -m venv venv
  ) else (
    python -m venv venv
  )
  if not exist "%VENV_PY%" (
    echo.
    echo ERROR: Python was not found on this computer.
    echo Please install Python 3.10 or newer from https://www.python.org/downloads/
    echo IMPORTANT: tick "Add Python to PATH" during installation, then run start.bat again.
    pause
    exit /b 1
  )
)

REM -- 3. Install dependencies if any are missing --
"%VENV_PY%" -c "import fastapi, uvicorn, requests, multipart, dotenv" >nul 2>&1
if errorlevel 1 (
  echo Installing required packages...
  "%VENV_PY%" -m pip install --upgrade pip >nul 2>&1
  "%VENV_PY%" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo.
    echo ERROR: Package installation failed. Check your internet connection and try again.
    pause
    exit /b 1
  )
)

REM -- 4. Free port 8000 if an old PlantPulse server is still running --
for /f "tokens=5" %%p in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":8000 "') do (
  echo Stopping old server on port 8000 - PID %%p
  taskkill /f /pid %%p >nul 2>&1
)
ping -n 2 127.0.0.1 >nul

REM -- 5. Start PlantPulse --
set "LAN_IP="
for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "$ip=(Get-NetIPAddress -AddressFamily IPv4 ^| Where-Object {$_.IPAddress -notlike '127.*' -and $_.InterfaceOperationalStatus -eq 'Up' -and $_.PrefixOrigin -ne 'WellKnown'} ^| Sort-Object InterfaceMetric ^| Select-Object -First 1 -ExpandProperty IPAddress); $ip"`) do set "LAN_IP=%%i"
echo.
echo PlantPulse is starting...
echo This computer: http://localhost:8000
if defined LAN_IP echo Local network: http://%LAN_IP%:8000
echo If another device cannot connect, run setup-lan.bat once as Administrator.
echo Press Ctrl+C in this window to stop the server.
echo.
"%VENV_PY%" main.py
pause
