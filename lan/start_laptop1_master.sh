#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════
#  ShardVault — LAPTOP 1 (MASTER) — Linux/Mac startup
#  Starts: Auth Service, Metadata DB, Orchestrator
# ════════════════════════════════════════════════════════════

set -e
cd "$(dirname "$0")"

echo "[ShardVault] Installing dependencies..."
pip3 install -r requirements.txt -q

mkdir -p data

echo "[ShardVault] Starting Auth Service (port 5001)..."
AUTH_PORT=5001 python3 auth_service.py &
AUTH_PID=$!
sleep 1

echo "[ShardVault] Starting Metadata DB (port 5005)..."
META_PORT=5005 META_DB_PATH=./data/metadata.db python3 metadata_db.py &
META_PID=$!
sleep 1

echo "[ShardVault] Starting Orchestrator + UI (port 5000)..."
python3 orchestrator.py &
ORCH_PID=$!

echo ""
echo "════════════════════════════════════════════════════════"
echo "  MASTER RUNNING"
echo "  UI:  http://localhost:5000"
echo "  PIDs: auth=$AUTH_PID meta=$META_PID orch=$ORCH_PID"
echo "════════════════════════════════════════════════════════"
echo "  Press Ctrl+C to stop all services"
echo ""

trap "kill $AUTH_PID $META_PID $ORCH_PID 2>/dev/null; echo '[ShardVault] All services stopped'" EXIT
wait
