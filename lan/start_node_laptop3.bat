@echo off
REM ════════════════════════════════════════════════════════════════
REM  ShardVault — LAPTOP 3 (Storage Nodes B + C) Startup Script
REM  Runs: storage-node-1 on port 5003
REM         storage-node-2 on port 5004
REM
REM  BEFORE RUNNING:
REM    Open firewall for ports 5003 and 5004:
REM    netsh advfirewall firewall add rule name="ShardVault-NodeBC" dir=in action=allow protocol=tcp localport=5003,5004
REM ════════════════════════════════════════════════════════════════

cd /d "%~dp0"

echo [ShardVault] Installing dependencies...
pip install -r requirements.txt --quiet

echo.
echo [ShardVault] Starting Storage Node B (storage-node-1) on port 5003...
set JWT_SECRET=shard_secret_key_2024_distributed
set NODE_NAME=storage-node-1
set PORT=5003
set SHARD_DIR=./data/shards_b
start "ShardVault Node-B" cmd /k "set NODE_NAME=storage-node-1 && set PORT=5003 && set SHARD_DIR=./data/shards_b && python storage_node.py"
timeout /t 2 /nobreak >nul

echo [ShardVault] Starting Storage Node C (storage-node-2) on port 5004...
start "ShardVault Node-C" cmd /k "set NODE_NAME=storage-node-2 && set PORT=5004 && set SHARD_DIR=./data/shards_c && python storage_node.py"
timeout /t 2 /nobreak >nul

echo.
echo ════════════════════════════════════════════════════════
echo  LAPTOP 3 STORAGE NODES STARTED
echo  Node B (storage-node-1): port 5003
echo  Node C (storage-node-2): port 5004
echo ════════════════════════════════════════════════════════
echo.
pause
