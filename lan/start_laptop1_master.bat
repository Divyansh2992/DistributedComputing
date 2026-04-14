@echo off
REM ════════════════════════════════════════════════════════════════
REM  ShardVault — LAPTOP 1 (MASTER) Startup Script
REM  Run this on the machine that hosts:
REM    • Orchestrator  (port 5000)
REM    • Auth Service  (port 5001)
REM    • Metadata DB   (port 5005)
REM    • UI            (served by orchestrator at port 5000)
REM
REM  BEFORE RUNNING:
REM    1. Edit config.py — set MASTER_IP and all node URLs
REM    2. Open firewall for ports 5000, 5001, 5005
REM       (run as admin: netsh advfirewall firewall add rule name="ShardVault" dir=in action=allow protocol=tcp localport=5000,5001,5005)
REM ════════════════════════════════════════════════════════════════

cd /d "%~dp0"

echo [ShardVault] Installing dependencies...
pip install -r requirements.txt --quiet

echo.
echo [ShardVault] Starting Auth Service on port 5001...
start "ShardVault Auth" cmd /k "python auth_service.py"
timeout /t 2 /nobreak >nul

echo [ShardVault] Starting Metadata DB on port 5005...
start "ShardVault MetaDB" cmd /k "python metadata_db.py"
timeout /t 2 /nobreak >nul

echo [ShardVault] Starting Orchestrator + UI on port 5000...
start "ShardVault Orchestrator" cmd /k "python orchestrator.py"
timeout /t 3 /nobreak >nul

echo.
echo ════════════════════════════════════════════════════════
echo  LAPTOP 1 SERVICES STARTED
echo  Access UI at: http://localhost:5000
echo  Share with other devices: http://YOUR_LAN_IP:5000
echo ════════════════════════════════════════════════════════
echo.
echo  Next: Start storage nodes on Laptop 2 and Laptop 3
echo        using start_node_laptop2.bat / start_node_laptop3.bat
echo.
pause
