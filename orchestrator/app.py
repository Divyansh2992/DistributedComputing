import os
import uuid
import base64
import hashlib
import requests
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder='static')
CORS(app)

JWT_SECRET  = os.environ.get('JWT_SECRET', 'shard_secret_key_2024_distributed')
AUTH_URL    = os.environ.get('AUTH_URL',   'http://auth_service:5001')
META_URL    = os.environ.get('META_URL',   'http://metadata_db:5005')
NODES = {
    'node_a': os.environ.get('NODE_A_URL', 'http://node_a:5002'),
    'node_b': os.environ.get('NODE_B_URL', 'http://node_b:5003'),
    'node_c': os.environ.get('NODE_C_URL', 'http://node_c:5004'),
}

_cached_token = None


# ─── Helpers ─────────────────────────────────────────────────────────────────

def get_token():
    global _cached_token
    try:
        r = requests.post(f'{AUTH_URL}/token', timeout=5)
        _cached_token = r.json()['token']
    except Exception as e:
        print(f'[AUTH] Failed to get token: {e}')
        _cached_token = None
    return _cached_token


def auth_headers():
    token = get_token()
    return {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}


def sha256(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()


def format_bytes(n):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if n < 1024:
            return f'{n:.1f} {unit}'
        n /= 1024
    return f'{n:.1f} TB'


# ─── Routes: Static ──────────────────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')


# ─── Routes: Health ──────────────────────────────────────────────────────────

@app.route('/health', methods=['GET'])
def health():
    statuses = {}
    for name, url in NODES.items():
        try:
            r = requests.get(f'{url}/health', timeout=2)
            statuses[name] = r.json()
        except Exception:
            statuses[name] = {'status': 'down', 'node': name}

    try:
        r = requests.get(f'{AUTH_URL}/health', timeout=2)
        auth_status = r.json()
    except Exception:
        auth_status = {'status': 'down'}

    try:
        r = requests.get(f'{META_URL}/health', timeout=2)
        meta_status = r.json()
    except Exception:
        meta_status = {'status': 'down'}

    return jsonify({
        'orchestrator': {'status': 'ok', 'service': 'orchestrator'},
        'auth_service': auth_status,
        'metadata_db':  meta_status,
        'nodes': statuses,
    })


# ─── Routes: Upload ──────────────────────────────────────────────────────────

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    f = request.files['file']
    filename     = f.filename or 'unnamed'
    content_type = f.content_type or 'application/octet-stream'
    raw_bytes    = f.read()
    original_size = len(raw_bytes)

    print(f'[UPLOAD] File: "{filename}" | Size: {format_bytes(original_size)} | Type: {content_type}')

    # Encode entire file as base64 so every file type is shardable
    b64 = base64.b64encode(raw_bytes).decode('utf-8')
    L   = len(b64)
    print(f'[ENCODE] Base64 length: {L} chars')

    # Split into 3 equal parts
    parts = [b64[0:L//3], b64[L//3: 2*L//3], b64[2*L//3:]]
    hashes = [sha256(p) for p in parts]
    print(f'[SPLIT]  Shard 0: {len(parts[0])} chars | Shard 1: {len(parts[1])} chars | Shard 2: {len(parts[2])} chars')

    file_id   = str(uuid.uuid4())
    shard_ids = [f'{file_id}_shard_{i}' for i in range(3)]
    parity_id = f'{file_id}_parity_1'  # duplicate of shard #1, stored on node_c
    print(f'[FILE_ID] {file_id}')

    node_order = ['node_a', 'node_b', 'node_c']
    hdrs       = auth_headers()
    store_errors = []

    # Store primary shards
    for i, (sid, part, node_name) in enumerate(zip(shard_ids, parts, node_order)):
        try:
            print(f'[SHARD {i}] Sending to {node_name} ({NODES[node_name]}) — {len(part)} chars — SHA256: {hashes[i][:16]}...')
            r = requests.post(
                f'{NODES[node_name]}/shards',
                json={'shard_id': sid, 'data': part},
                headers=hdrs, timeout=10
            )
            if r.status_code == 200:
                print(f'[SHARD {i}] Stored OK on {node_name}')
            else:
                err = f'Shard {i} on {node_name}: HTTP {r.status_code} — {r.text}'
                store_errors.append(err)
                print(f'[ERROR]  {err}')
        except Exception as e:
            err = f'Cannot reach {node_name}: {e}'
            store_errors.append(err)
            print(f'[ERROR]  {err}')

    # Store parity shard (copy of part[1]) → node_c alongside shard 2
    try:
        print(f'[PARITY] Sending copy of Shard 1 to node_c as parity — {len(parts[1])} chars')
        r = requests.post(
            f'{NODES["node_c"]}/shards',
            json={'shard_id': parity_id, 'data': parts[1]},
            headers=hdrs, timeout=10
        )
        if r.status_code == 200:
            print('[PARITY] Stored OK on node_c')
        else:
            store_errors.append(f'Parity store failed: HTTP {r.status_code}')
    except Exception as e:
        store_errors.append(f'Parity store failed: {e}')
        print(f'[ERROR]  Parity: {e}')

    # Persist metadata
    metadata = {
        'file_id':       file_id,
        'filename':      filename,
        'original_size': original_size,
        'content_type':  content_type,
        'shard_count':   3,
        'shards': [
            {'shard_id': shard_ids[0], 'shard_index': 0,
             'node_url': NODES['node_a'], 'node_name': 'node_a',
             'hash': hashes[0], 'is_parity': False},
            {'shard_id': shard_ids[1], 'shard_index': 1,
             'node_url': NODES['node_b'], 'node_name': 'node_b',
             'hash': hashes[1], 'is_parity': False},
            {'shard_id': shard_ids[2], 'shard_index': 2,
             'node_url': NODES['node_c'], 'node_name': 'node_c',
             'hash': hashes[2], 'is_parity': False},
            {'shard_id': parity_id, 'shard_index': 1,
             'node_url': NODES['node_c'], 'node_name': 'node_c',
             'hash': hashes[1], 'is_parity': True, 'parity_for_index': 1},
        ]
    }

    try:
        print(f'[META]   Saving recipe to metadata_db...')
        requests.post(f'{META_URL}/files', json=metadata, timeout=10)
        print(f'[META]   Recipe saved OK')
    except Exception as e:
        print(f'[ERROR]  Metadata save failed: {e}')
        return jsonify({'error': f'Metadata save failed: {e}'}), 500

    print(f'[DONE]   Upload complete. Errors: {store_errors if store_errors else "none"}')
    return jsonify({
        'status':        'uploaded',
        'file_id':       file_id,
        'filename':      filename,
        'original_size': original_size,
        'original_size_human': format_bytes(original_size),
        'shard_hashes':  hashes,
        'parity_stored': True,
        'errors':        store_errors,
    })


# ─── Routes: List files ──────────────────────────────────────────────────────

@app.route('/files', methods=['GET'])
def list_files():
    try:
        r = requests.get(f'{META_URL}/files', timeout=5)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ─── Routes: Shard Explorer (peek at live node data) ─────────────────────────

@app.route('/files/<file_id>/peek', methods=['GET'])
def peek_shards(file_id):
    """Fetch live shard info from each storage node for the Shard Explorer UI."""
    try:
        r = requests.get(f'{META_URL}/files/{file_id}', timeout=5)
        if r.status_code == 404:
            return jsonify({'error': 'File not found'}), 404
        meta = r.json()
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    hdrs   = auth_headers()
    result = []

    for shard in meta['shards']:
        info = {
            'shard_id':        shard['shard_id'],
            'shard_index':     shard['shard_index'],
            'node_name':       shard['node_name'],
            'node_url':        shard['node_url'],
            'hash':            shard['hash'],
            'is_parity':       shard['is_parity'],
            'parity_for_index': shard.get('parity_for_index'),
            'preview':         None,
            'stored_size':     0,
            'status':          'unknown',
        }
        try:
            r = requests.get(
                f'{shard["node_url"]}/shards/{shard["shard_id"]}',
                headers=hdrs, timeout=3
            )
            if r.status_code == 200:
                data = r.json()['data']
                info['preview']     = data[:120]   # first 120 base64 chars
                info['stored_size'] = len(data)
                info['status']      = 'ok'
            else:
                info['status'] = 'not_found'
        except Exception as e:
            info['status'] = 'node_down'
            info['error']  = str(e)

        result.append(info)

    return jsonify({
        'file_id':       file_id,
        'filename':      meta['filename'],
        'original_size': meta['original_size'],
        'content_type':  meta['content_type'],
        'shards':        result,
    })


# ─── Routes: Download (fault-tolerant + checksum) ────────────────────────────

@app.route('/download/<file_id>', methods=['GET'])
def download_file(file_id):
    # 1. Fetch recipe
    try:
        r = requests.get(f'{META_URL}/files/{file_id}', timeout=5)
        if r.status_code == 404:
            return jsonify({'error': 'File not found'}), 404
        meta = r.json()
    except Exception as e:
        return jsonify({'error': f'Metadata unreachable: {e}'}), 500

    shards_info    = meta['shards']
    primary_shards = sorted([s for s in shards_info if not s['is_parity']], key=lambda x: x['shard_index'])
    parity_shards  = [s for s in shards_info if s['is_parity']]

    hdrs = auth_headers()
    log  = []
    corrupted = []
    recovered = []
    parts = {}

    # 2. Retrieve each shard (with parity fallback)
    for shard in primary_shards:
        idx          = shard['shard_index']
        expected_hash = shard['hash']
        data         = None
        source       = None

        # Try primary node
        try:
            r = requests.get(
                f'{shard["node_url"]}/shards/{shard["shard_id"]}',
                headers=hdrs, timeout=3
            )
            if r.status_code == 200:
                data   = r.json()['data']
                source = shard['node_name']
        except Exception as e:
            log.append(f'[WARN] Primary {shard["node_name"]} DOWN for shard {idx}: {e}')

        # Fallback to parity
        if data is None:
            par = next((p for p in parity_shards if p.get('parity_for_index') == idx), None)
            if par:
                try:
                    r = requests.get(
                        f'{par["node_url"]}/shards/{par["shard_id"]}',
                        headers=hdrs, timeout=3
                    )
                    if r.status_code == 200:
                        data   = r.json()['data']
                        source = f'{par["node_name"]} (PARITY RECOVERY)'
                        recovered.append(idx)
                        log.append(f'[RECOVERY] Shard {idx} recovered via parity on {par["node_name"]}')
                except Exception as e:
                    log.append(f'[ERROR] Parity node also failed for shard {idx}: {e}')

        if data is None:
            return jsonify({
                'error': f'IRRECOVERABLE — Shard {idx} lost with no parity available',
                'log': log,
            }), 503

        # 3. SHA-256 integrity check
        actual_hash = sha256(data)
        if actual_hash != expected_hash:
            corrupted.append(idx)
            log.append(f'[CORRUPT] Shard {idx} checksum MISMATCH! (source: {source})')
        else:
            log.append(f'[OK] Shard {idx} integrity verified — source: {source}')

        parts[idx] = data

    # 4. Reconstruct — handle corrupted (non-base64) shards gracefully
    b64_full = parts[0] + parts[1] + parts[2]
    try:
        file_bytes = base64.b64decode(b64_full)
    except Exception as decode_err:
        log.append(f'[ERROR] Base64 decode failed (corrupted shard data): {decode_err}')
        return jsonify({
            'filename':         meta['filename'],
            'log':              log,
            'corrupted_shards': corrupted,
            'recovered_shards': recovered,
            'error':            f'Reconstruction failed — corrupted shard(s): {corrupted}. '
                                f'Integrity check detected the corruption.',
        }), 422

    return jsonify({
        'filename':         meta['filename'],
        'content_type':     meta['content_type'],
        'size':             len(file_bytes),
        'log':              log,
        'corrupted_shards': corrupted,
        'recovered_shards': recovered,
        'data_b64':         base64.b64encode(file_bytes).decode('utf-8'),
    })


# ─── Routes: Delete ──────────────────────────────────────────────────────────

@app.route('/files/<file_id>', methods=['DELETE'])
def delete_file(file_id):
    try:
        r = requests.get(f'{META_URL}/files/{file_id}', timeout=5)
        meta = r.json()
    except Exception:
        return jsonify({'error': 'File not found'}), 404

    hdrs = auth_headers()
    for shard in meta.get('shards', []):
        try:
            requests.delete(
                f'{shard["node_url"]}/shards/{shard["shard_id"]}',
                headers=hdrs, timeout=3
            )
        except Exception:
            pass

    try:
        requests.delete(f'{META_URL}/files/{file_id}', timeout=5)
    except Exception:
        pass

    return jsonify({'status': 'deleted', 'file_id': file_id})


# ─── Routes: Demo — corrupt a shard ─────────────────────────────────────────

@app.route('/demo/corrupt/<file_id>/<int:shard_index>', methods=['POST'])
def corrupt_demo(file_id, shard_index):
    try:
        r = requests.get(f'{META_URL}/files/{file_id}', timeout=5)
        meta = r.json()
    except Exception:
        return jsonify({'error': 'File not found'}), 404

    hdrs = auth_headers()
    target = next(
        (s for s in meta['shards'] if not s['is_parity'] and s['shard_index'] == shard_index),
        None
    )
    if not target:
        return jsonify({'error': f'Shard {shard_index} not found'}), 404

    try:
        r = requests.post(
            f'{target["node_url"]}/shards/{target["shard_id"]}/corrupt',
            headers=hdrs, timeout=3
        )
        return jsonify({'status': 'shard_corrupted', 'shard_index': shard_index, 'node': target['node_name']})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
