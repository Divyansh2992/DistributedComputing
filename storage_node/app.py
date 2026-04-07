import os
import jwt
from flask import Flask, request, jsonify
from functools import wraps

app = Flask(__name__)
SECRET_KEY = os.environ.get('JWT_SECRET', 'shard_secret_key_2024_distributed')
NODE_NAME = os.environ.get('NODE_NAME', 'node_unknown')
STORAGE_DIR = '/data/shards'

os.makedirs(STORAGE_DIR, exist_ok=True)


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return jsonify({'error': 'Missing Authorization header'}), 401
        token = auth_header.split(' ', 1)[1]
        try:
            jwt.decode(token, SECRET_KEY, algorithms=['HS256'])
        except jwt.InvalidTokenError as e:
            return jsonify({'error': f'Invalid token: {e}'}), 401
        return f(*args, **kwargs)
    return decorated


@app.route('/health', methods=['GET'])
def health():
    try:
        shards = os.listdir(STORAGE_DIR)
        count = len(shards)
    except Exception:
        count = 0
    return jsonify({'status': 'ok', 'node': NODE_NAME, 'shard_count': count})


@app.route('/shards', methods=['POST'])
@require_auth
def store_shard():
    data = request.get_json()
    shard_id = data.get('shard_id')
    shard_data = data.get('data')
    if not shard_id or shard_data is None:
        return jsonify({'error': 'shard_id and data are required'}), 400
    filepath = os.path.join(STORAGE_DIR, shard_id)
    with open(filepath, 'w') as f:
        f.write(shard_data)
    return jsonify({'status': 'stored', 'shard_id': shard_id, 'node': NODE_NAME})


@app.route('/shards/<shard_id>', methods=['GET'])
@require_auth
def retrieve_shard(shard_id):
    filepath = os.path.join(STORAGE_DIR, shard_id)
    if not os.path.exists(filepath):
        return jsonify({'error': 'Shard not found'}), 404
    with open(filepath, 'r') as f:
        data = f.read()
    return jsonify({'shard_id': shard_id, 'data': data, 'node': NODE_NAME})


@app.route('/shards/<shard_id>', methods=['DELETE'])
@require_auth
def delete_shard(shard_id):
    filepath = os.path.join(STORAGE_DIR, shard_id)
    if os.path.exists(filepath):
        os.remove(filepath)
    return jsonify({'status': 'deleted', 'shard_id': shard_id})


@app.route('/shards/<shard_id>/corrupt', methods=['POST'])
@require_auth
def corrupt_shard(shard_id):
    """Demo endpoint: inject garbage data to trigger checksum failure."""
    filepath = os.path.join(STORAGE_DIR, shard_id)
    if not os.path.exists(filepath):
        return jsonify({'error': 'Shard not found'}), 404
    with open(filepath, 'w') as f:
        f.write('CORRUPTED_GARBAGE_DATA_XYZ_SHARDVAULT')
    return jsonify({'status': 'corrupted', 'shard_id': shard_id, 'node': NODE_NAME})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5002))
    app.run(host='0.0.0.0', port=port)
