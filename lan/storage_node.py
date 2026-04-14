"""
ShardVault LAN — Storage Node
==============================
Run on each storage laptop (Laptop 2, Laptop 3):

  # Node A on Laptop 2 (port 5002)
  NODE_NAME=storage-node-0 PORT=5002 SHARD_DIR=./data/shards python storage_node.py

  # Node B on Laptop 3 (port 5003)
  NODE_NAME=storage-node-1 PORT=5003 SHARD_DIR=./data/shards python storage_node.py

  # Node C on Laptop 3 (port 5004) — optional second node on same machine
  NODE_NAME=storage-node-2 PORT=5004 SHARD_DIR=./data/shards_c python storage_node.py

APIs:
  GET  /health
  GET  /metrics
  POST /shards                         — store shard
  GET  /shards/<shard_id>              — retrieve shard
  DEL  /shards/<shard_id>              — delete shard
  GET  /shards                         — list all shards
  POST /shards/<shard_id>/corrupt      — inject corruption (demo)
"""

import os
import sys
import time
import jwt
from flask import Flask, request, jsonify
from flask_cors import CORS
from functools import wraps
from prometheus_client import Counter, Gauge, generate_latest, CONTENT_TYPE_LATEST

# ─── Config ──────────────────────────────────────────────────────────────────
SECRET_KEY  = os.environ.get('JWT_SECRET', 'shard_secret_key_2024_distributed')
NODE_NAME   = os.environ.get('NODE_NAME',  'storage-node-unknown').strip()  # strip trailing spaces from bat files
SHARD_DIR   = os.environ.get('SHARD_DIR',  './data/shards').strip()
PORT        = int(os.environ.get('PORT',   5002))

os.makedirs(SHARD_DIR, exist_ok=True)

app = Flask(__name__)
CORS(app)

# ─── Prometheus ──────────────────────────────────────────────────────────────
SHARD_WRITES  = Counter('shardvault_shard_writes_total',  'Total shard writes',  ['node'])
SHARD_READS   = Counter('shardvault_shard_reads_total',   'Total shard reads',   ['node'])
SHARD_DELETES = Counter('shardvault_shard_deletes_total', 'Total shard deletes', ['node'])
SHARD_COUNT   = Gauge  ('shardvault_shard_count',         'Current shard count', ['node'])
AUTH_FAILURES = Counter('shardvault_auth_failures_total', 'JWT auth failures',   ['node'])


def update_shard_count():
    try:
        SHARD_COUNT.labels(node=NODE_NAME).set(len(os.listdir(SHARD_DIR)))
    except Exception:
        pass


# ─── Auth ─────────────────────────────────────────────────────────────────────

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        hdr = request.headers.get('Authorization', '')
        if not hdr.startswith('Bearer '):
            AUTH_FAILURES.labels(node=NODE_NAME).inc()
            return jsonify({'error': 'Missing Authorization header'}), 401
        token = hdr.split(' ', 1)[1]
        try:
            jwt.decode(token, SECRET_KEY, algorithms=['HS256'])
        except jwt.InvalidTokenError as e:
            AUTH_FAILURES.labels(node=NODE_NAME).inc()
            return jsonify({'error': f'Invalid token: {e}'}), 401
        return f(*args, **kwargs)
    return decorated


# ─── Routes ──────────────────────────────────────────────────────────────────

@app.route('/health', methods=['GET'])
def health():
    count   = 0
    free_gb = 0.0
    try:
        os.makedirs(SHARD_DIR, exist_ok=True)  # ensure it exists
        count = len(os.listdir(SHARD_DIR))
    except Exception:
        pass
    try:
        import shutil
        free_gb = round(shutil.disk_usage(SHARD_DIR).free / (1024 ** 3), 2)
    except Exception:
        try:
            # fallback: statvfs on Linux/Mac
            disk    = os.statvfs(SHARD_DIR)
            free_gb = round((disk.f_frsize * disk.f_bavail) / (1024 ** 3), 2)
        except Exception:
            free_gb = 0.0
    update_shard_count()
    return jsonify({
        'status':      'ok',
        'node':        NODE_NAME,
        'shard_count': count,
        'free_gb':     free_gb,
    })


@app.route('/metrics')
def metrics_endpoint():
    update_shard_count()
    return generate_latest(), 200, {'Content-Type': CONTENT_TYPE_LATEST}


@app.route('/shards', methods=['POST'])
@require_auth
def store_shard():
    try:
        data       = request.get_json(force=True)
        shard_id   = data.get('shard_id')
        shard_data = data.get('data')
        if not shard_id or shard_data is None:
            return jsonify({'error': 'shard_id and data required'}), 400
        os.makedirs(SHARD_DIR, exist_ok=True)   # guaranteed even if dir was deleted at runtime
        filepath = os.path.join(SHARD_DIR, shard_id)
        with open(filepath, 'w', encoding='utf-8') as fh:
            fh.write(shard_data)
        SHARD_WRITES.labels(node=NODE_NAME).inc()
        update_shard_count()
        print(f'[STORE] {shard_id} ({len(shard_data)} chars)')
        return jsonify({'status': 'stored', 'shard_id': shard_id, 'node': NODE_NAME})
    except Exception as e:
        import traceback
        print(f'[ERROR] store_shard failed: {e}\n{traceback.format_exc()}')
        return jsonify({'error': f'store_shard failed: {e}'}), 500


@app.route('/shards/<shard_id>', methods=['GET'])
@require_auth
def retrieve_shard(shard_id):
    filepath = os.path.join(SHARD_DIR, shard_id)
    if not os.path.exists(filepath):
        return jsonify({'error': 'Shard not found'}), 404
    with open(filepath, 'r', encoding='utf-8') as fh:
        data = fh.read()
    SHARD_READS.labels(node=NODE_NAME).inc()
    return jsonify({'shard_id': shard_id, 'data': data, 'node': NODE_NAME})


@app.route('/shards/<shard_id>', methods=['DELETE'])
@require_auth
def delete_shard(shard_id):
    filepath = os.path.join(SHARD_DIR, shard_id)
    if os.path.exists(filepath):
        os.remove(filepath)
        SHARD_DELETES.labels(node=NODE_NAME).inc()
    update_shard_count()
    return jsonify({'status': 'deleted', 'shard_id': shard_id})


@app.route('/shards', methods=['GET'])
@require_auth
def list_shards():
    try:
        shards = os.listdir(SHARD_DIR)
    except Exception:
        shards = []
    return jsonify({'node': NODE_NAME, 'shards': shards, 'count': len(shards)})


@app.route('/shards/<shard_id>/corrupt', methods=['POST'])
@require_auth
def corrupt_shard(shard_id):
    """Demo: overwrite shard with garbage to trigger checksum failure."""
    filepath = os.path.join(SHARD_DIR, shard_id)
    if not os.path.exists(filepath):
        return jsonify({'error': 'Shard not found'}), 404
    with open(filepath, 'w', encoding='utf-8') as fh:
        fh.write(f'CORRUPTED_GARBAGE_{NODE_NAME}_{time.time()}')
    print(f'[CORRUPT] {shard_id} intentionally corrupted (demo)')
    return jsonify({'status': 'corrupted', 'shard_id': shard_id, 'node': NODE_NAME})


# ─── Run ─────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    print(f"""
╔════════════════════════════════════════╗
║  ShardVault Storage Node  —  LAN Mode ║
╠════════════════════════════════════════╣
║  Node Name : {NODE_NAME:<26} ║
║  Port      : {PORT:<26} ║
║  Shard Dir : {SHARD_DIR:<26} ║
║  Listening : http://0.0.0.0:{PORT}      ║
╚════════════════════════════════════════╝
""")
    app.run(host='0.0.0.0', port=PORT, debug=False)
