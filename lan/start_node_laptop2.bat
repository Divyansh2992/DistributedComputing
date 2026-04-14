@echo off
REM ════════════════════════════════════════════════════════════════
REM  ShardVault — LAPTOP 2 (Storage Node A) Startup Script
REM  Runs: storage-node-0 on port 5002
REM
REM  BEFORE RUNNING:
REM    1. Set MASTER_IP below to Laptop 1's LAN IP
REM    2. Open firewall for port 5002
REM       netsh advfirewall firewall add rule name="ShardVault-NodeA" dir=in action=allow protocol=tcp localport=5002
REM ════════════════════════════════════════════════════════════════

cd /d "%~dp0"

REM ── CONFIGURE THESE ──────────────────────────────────────────────
set JWT_SECRET=shard_secret_key_2024_distributed
set NODE_NAME=storage-node-0
set PORT=5002
set SHARD_DIR=./data/shards_a
REM ────────────────────────────────────────────────────────────────

echo [ShardVault] Installing dependencies...
pip install -r requirements.txt --quiet

echo.
echo [ShardVault] Starting Storage Node A (storage-node-0) on port 5002...
echo   Shards stored in: %SHARD_DIR%
echo.

python storage_node.py
pause
