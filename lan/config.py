"""
ShardVault LAN Configuration
============================
Edit this file before starting any service.

LAPTOP 1 (MASTER): Runs orchestrator, auth_service, metadata_db
LAPTOP 2         : Runs storage node A  (NODE_A_URL)
LAPTOP 3         : Runs storage node B  (NODE_B_URL)

If you only have 2 laptops:
  - Run Node A on Laptop 2
  - Run Node B AND Node C on Laptop 2 (different ports)

Find your LAN IP on Windows:  ipconfig | findstr IPv4
Find your LAN IP on Linux/Mac: hostname -I
"""

import os

# ─── Master Node (Laptop 1) ──────────────────────────────────────────────────
MASTER_IP   = os.environ.get("MASTER_IP",   "10.113.220.51")  # ← CHANGE THIS

# ─── Auth Service ─────────────────────────────────────────────────────────────
AUTH_URL    = os.environ.get("AUTH_URL",    f"http://{MASTER_IP}:5001")

# ─── Metadata DB ──────────────────────────────────────────────────────────────
META_URL    = os.environ.get("META_URL",    f"http://{MASTER_IP}:5005")

# ─── Storage Nodes ────────────────────────────────────────────────────────────
# Each entry: (node_name, node_url)
# node_name must match NODE_NAME env var set when starting that node's process.
NODE_A_URL  = os.environ.get("NODE_A_URL",  "http://10.113.220.214:5002")  # ← Laptop 2
NODE_B_URL  = os.environ.get("NODE_B_URL",  "http://10.113.220.214:5003")  # ← Laptop 3
NODE_C_URL  = os.environ.get("NODE_C_URL",  "http://10.113.220.214:5004")  # ← Laptop 3 second node

# ─── JWT ──────────────────────────────────────────────────────────────────────
JWT_SECRET  = os.environ.get("JWT_SECRET",  "shard_secret_key_2024_distributed")

# ─── Orchestrator ─────────────────────────────────────────────────────────────
ORCHESTRATOR_PORT = int(os.environ.get("ORCHESTRATOR_PORT", 5000))

# ─── Metadata DB (local path) ─────────────────────────────────────────────────
META_DB_PATH = os.environ.get("META_DB_PATH", "./data/metadata.db")

# ─── Storage Node (local path) ────────────────────────────────────────────────
SHARD_DIR   = os.environ.get("SHARD_DIR",  "./data/shards")

# ─── Node registry for orchestrator ──────────────────────────────────────────
# Format: { 'display_name': url }
# The display_name must match what each node returns in its NODE_NAME env var
NODES = {
    "storage-node-0": NODE_A_URL,
    "storage-node-1": NODE_B_URL,
    "storage-node-2": NODE_C_URL,
}
