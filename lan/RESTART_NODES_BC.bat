@echo off
REM ══════════════════════════════════════════════════════════════
REM  ShardVault — LAPTOP 3 QUICK RESTART (Nodes B + C)
REM  Ports 5003 and 5004
REM  Copy this file + storage_node.py to the same folder and run.
REM ══════════════════════════════════════════════════════════════
cd /d "%~dp0"

REM Create shard directories
if not exist "data\shards_b" mkdir "data\shards_b"
if not exist "data\shards_c" mkdir "data\shards_c"

echo [ShardVault] Installing/updating dependencies...
pip install flask flask-cors PyJWT prometheus_client --quiet

echo.
echo [ShardVault] Starting Node B (storage-node-1) on port 5003...
start "ShardVault Node-B :5003" cmd /k "cd /d "%~dp0" && set JWT_SECRET=shard_secret_key_2024_distributed && set NODE_NAME=storage-node-1 && set PORT=5003 && set SHARD_DIR=%~dp0data\shards_b && python "%~dp0storage_node.py""
timeout /t 3 /nobreak >nul

echo [ShardVault] Starting Node C (storage-node-2) on port 5004...
start "ShardVault Node-C :5004" cmd /k "cd /d "%~dp0" && set JWT_SECRET=shard_secret_key_2024_distributed && set NODE_NAME=storage-node-2 && set PORT=5004 && set SHARD_DIR=%~dp0data\shards_c && python "%~dp0storage_node.py""

echo.
echo ══════════════════════════════════════════════════════
echo   Nodes B + C started in separate windows.
echo   Node B: http://0.0.0.0:5003
echo   Node C: http://0.0.0.0:5004
echo   Close those windows to stop the nodes.
echo ══════════════════════════════════════════════════════
pause
