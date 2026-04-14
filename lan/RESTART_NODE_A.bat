@echo off
REM ══════════════════════════════════════════════════════════════
REM  ShardVault — LAPTOP 2 QUICK RESTART (Node A only, port 5002)
REM  Copy this file + storage_node.py to the same folder and run.
REM ══════════════════════════════════════════════════════════════
cd /d "%~dp0"

REM Create shard directory
if not exist "data\shards_a" mkdir "data\shards_a"

echo [ShardVault] Installing/updating dependencies...
pip install flask flask-cors PyJWT prometheus_client --quiet

echo.
echo [ShardVault] Starting Node A (storage-node-0) on port 5002
echo   Shard dir: %~dp0data\shards_a
echo   Press Ctrl+C to stop.
echo.

set JWT_SECRET=shard_secret_key_2024_distributed
set NODE_NAME=storage-node-0
set PORT=5002
set SHARD_DIR=%~dp0data\shards_a

python "%~dp0storage_node.py"
pause
