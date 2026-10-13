@echo off
setlocal

REM Request administrator access only for the scoped Windows Firewall rule.
net session >nul 2>&1
if errorlevel 1 (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

netsh advfirewall firewall delete rule name="PlantPulse Local Network" >nul 2>&1
netsh advfirewall firewall add rule name="PlantPulse Local Network" dir=in action=allow protocol=TCP localport=8000 remoteip=LocalSubnet profile=private

if errorlevel 1 (
  echo Failed to create the firewall rule. Make sure this network is marked Private in Windows.
) else (
  echo PlantPulse is now allowed on TCP port 8000 for devices on your private local subnet only.
)
pause
