import os
import time
import jwt
from flask import Flask, request, jsonify
from functools import wraps
from prometheus_client import Counter, Gauge, generate_latest, CONTENT_TYPE_LATEST

app = Flask(__name__)
SECRET_KEY  = os.environ.get('JWT_SECRET', 'shard_secret_key_2024_distributed')
NODE_NAME   = os.environ.get('NODE_NAME', 'node_unknown')
STORAGE_DIR = '/data/shards'

os.makedirs(STORAGE_DIR, exist_ok=True)

# ─── Prometheus metrics ───────────────────────────────────────────────────────
SHARD_WRITES   = Counter('shardvault_shard_writes_total',   'Total shard writes',   ['node'])
SHARD_READS    = Counter('shardvault_shard_reads_total',    'Total shard reads',    ['node'])
SHARD_DELETES  = Counter('shardvault_shard_deletes_total',  'Total shard deletes',  ['node'])
SHARD_COUNT    = Gauge  ('shardvault_shard_count',          'Current shard count',  ['node'])
AUTH_FAILURES  = Counter('shardvault_auth_failures_total',  'JWT auth failures',    ['node'])


def update_shard_count():
    try:
        count = len(os.listdir(STORAGE_DIR))
        SHARD_COUNT.labels(node=NODE_NAME).set(count)
    except Exception:
        pass


# ─── Auth middleware ──────────────────────────────────────────────────────────

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            AUTH_FAILURES.labels(node=NODE_NAME).inc()
            return jsonify({'error': 'Missing Authorization header'}), 401
        token = auth_header.split(' ', 1)[1]
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
    try:
        shards = os.listdir(STORAGE_DIR)
        count  = len(shards)
        disk   = os.statvfs(STORAGE_DIR)
        free_gb = (disk.f_frsize * disk.f_bavail) / (1024 ** 3)
    except Exception:
        count, free_gb = 0, 0.0
    update_shard_count()
    return jsonify({
        'status':      'ok',
        'node':        NODE_NAME,
        'shard_count': count,
        'free_gb':     round(free_gb, 2),
    })


@app.route('/metrics')
def metrics():
    update_shard_count()
    return generate_latest(), 200, {'Content-Type': CONTENT_TYPE_LATEST}


@app.route('/shards', methods=['POST'])
@require_auth
def store_shard():
    data      = request.get_json()
    shard_id  = data.get('shard_id')
    shard_data = data.get('data')
    if not shard_id or shard_data is None:
        return jsonify({'error': 'shard_id and data are required'}), 400

    filepath = os.path.join(STORAGE_DIR, shard_id)
    with open(filepath, 'w') as f:
        f.write(shard_data)

    SHARD_WRITES.labels(node=NODE_NAME).inc()
    update_shard_count()
    return jsonify({'status': 'stored', 'shard_id': shard_id, 'node': NODE_NAME})


@app.route('/shards/<shard_id>', methods=['GET'])
@require_auth
def retrieve_shard(shard_id):
    filepath = os.path.join(STORAGE_DIR, shard_id)
    if not os.path.exists(filepath):
        return jsonify({'error': 'Shard not found'}), 404
    with open(filepath, 'r') as f:
        data = f.read()
    SHARD_READS.labels(node=NODE_NAME).inc()
    return jsonify({'shard_id': shard_id, 'data': data, 'node': NODE_NAME})


@app.route('/shards/<shard_id>', methods=['DELETE'])
@require_auth
def delete_shard(shard_id):
    filepath = os.path.join(STORAGE_DIR, shard_id)
    if os.path.exists(filepath):
        os.remove(filepath)
        SHARD_DELETES.labels(node=NODE_NAME).inc()
    update_shard_count()
    return jsonify({'status': 'deleted', 'shard_id': shard_id})


@app.route('/shards', methods=['GET'])
@require_auth
def list_shards():
    """List all shard IDs stored on this node."""
    try:
        shards = os.listdir(STORAGE_DIR)
    except Exception:
        shards = []
    return jsonify({'node': NODE_NAME, 'shards': shards, 'count': len(shards)})


@app.route('/shards/<shard_id>/corrupt', methods=['POST'])
@require_auth
def corrupt_shard(shard_id):
    """Demo endpoint: inject garbage to trigger checksum failure."""
    filepath = os.path.join(STORAGE_DIR, shard_id)
    if not os.path.exists(filepath):
        return jsonify({'error': 'Shard not found'}), 404
    with open(filepath, 'w') as f:
        f.write('CORRUPTED_GARBAGE_DATA_XYZ_SHARDVAULT_' + str(time.time()))
    return jsonify({'status': 'corrupted', 'shard_id': shard_id, 'node': NODE_NAME})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5002))
    app.run(host='0.0.0.0', port=port)
