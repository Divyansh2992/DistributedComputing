#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════
#  ShardVault — Storage Node Startup  (Linux/Mac)
#
#  Usage:
#    # Node A on Laptop 2 (port 5002):
#    NODE_NAME=storage-node-0 PORT=5002 SHARD_DIR=./data/shards_a ./start_node.sh
#
#    # Node B on Laptop 3 (port 5003):
#    NODE_NAME=storage-node-1 PORT=5003 SHARD_DIR=./data/shards_b ./start_node.sh
#
#    # Node C on Laptop 3 (port 5004) — second node, background:
#    NODE_NAME=storage-node-2 PORT=5004 SHARD_DIR=./data/shards_c python3 storage_node.py &
# ════════════════════════════════════════════════════════════

export NODE_NAME="${NODE_NAME:-storage-node-0}"
export PORT="${PORT:-5002}"
export SHARD_DIR="${SHARD_DIR:-./data/shards}"
export JWT_SECRET="${JWT_SECRET:-shard_secret_key_2024_distributed}"

cd "$(dirname "$0")"
pip3 install -r requirements.txt -q
mkdir -p "$SHARD_DIR"

echo "[ShardVault Node] $NODE_NAME | port $PORT | dir $SHARD_DIR"
python3 storage_node.py
