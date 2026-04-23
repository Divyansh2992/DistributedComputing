import os
import uuid
import base64
import hashlib
import time
import threading
import requests
import jwt as pyjwt
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST

app = Flask(__name__, static_folder='static')
CORS(app)

JWT_SECRET = os.environ.get('JWT_SECRET', 'shard_secret_key_2024_distributed')
AUTH_URL   = os.environ.get('AUTH_URL',   'http://auth_service:5001')
META_URL   = os.environ.get('META_URL',   'http://metadata_db:5005')

# ─── Node Registry ────────────────────────────────────────────────────────────
NODES = {
    'storage-node-0': os.environ.get('NODE_A_URL', 'http://localhost:5002'),
    'storage-node-1': os.environ.get('NODE_B_URL', 'http://localhost:5003'),
    'storage-node-2': os.environ.get('NODE_C_URL', 'http://localhost:5004'),
}

# ─── Prometheus Metrics ───────────────────────────────────────────────────────
UPLOAD_TOTAL      = Counter('shardvault_uploads_total',   'Total file uploads attempted')
UPLOAD_SUCCESS    = Counter('shardvault_uploads_success', 'Total file uploads completed successfully')
UPLOAD_FAILURE    = Counter('shardvault_uploads_failed',  'Total file uploads that failed (incl. rollback)')
DOWNLOAD_TOTAL    = Counter('shardvault_downloads_total',         'Total download requests')
DOWNLOAD_RECOVERY = Counter('shardvault_downloads_xor_recovered', 'Downloads that required XOR recovery')
DOWNLOAD_FAILURE  = Counter('shardvault_downloads_failed',        'Downloads that could not be served')
UPLOAD_LATENCY    = Histogram('shardvault_upload_duration_seconds',   'End-to-end upload latency',
                               buckets=[0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10])
DOWNLOAD_LATENCY  = Histogram('shardvault_download_duration_seconds', 'End-to-end download latency',
                               buckets=[0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10])
FILES_STORED      = Gauge('shardvault_files_stored_total', 'Current number of files in metadata DB')


@app.route('/metrics')
def metrics():
    try:
        r = requests.get(f'{META_URL}/files', timeout=3)
        FILES_STORED.set(len(r.json()))
    except Exception:
        pass
    return generate_latest(), 200, {'Content-Type': CONTENT_TYPE_LATEST}


# ─── Internal Service Token Cache ─────────────────────────────────────────────
_token_lock   = threading.Lock()
_cached_token = None
_token_expiry = 0


def get_token(force_refresh=False):
    """Get the long-lived inter-service JWT (used for node communication)."""
    global _cached_token, _token_expiry
    with _token_lock:
        if not force_refresh and _cached_token and time.time() < _token_expiry:
            return _cached_token
        try:
            r = requests.post(f'{AUTH_URL}/token', timeout=5)
            r.raise_for_status()
            _cached_token = r.json()['token']
            _token_expiry = time.time() + (24 * 3600) - 60
            return _cached_token
        except Exception as e:
            print(f'[AUTH] Token fetch failed: {e}')
            return _cached_token


def auth_headers():
    return {'Authorization': f'Bearer {get_token()}', 'Content-Type': 'application/json'}


# ─── User JWT Verification ────────────────────────────────────────────────────

def get_current_user(req):
    """
    Decode the user JWT from the Authorization header.
    Returns dict with keys: user_id, username, role
    Returns None if missing / invalid.
    """
    auth_header = req.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return None
    token = auth_header[len('Bearer '):]
    try:
        payload = pyjwt.decode(token, JWT_SECRET, algorithms=['HS256'])
        return {
            'user_id':  payload.get('sub'),
            'username': payload.get('username', 'unknown'),
            'role':     payload.get('role', 'user'),
        }
    except pyjwt.ExpiredSignatureError:
        return None
    except pyjwt.InvalidTokenError:
        return None


def require_auth(req):
    """Return (user_dict, None) on success, or (None, error_response) on failure."""
    user = get_current_user(req)
    if not user:
        return None, (jsonify({'error': 'Unauthorized — please log in'}), 401)
    return user, None


# ─── Utilities ────────────────────────────────────────────────────────────────

def sha256(data: bytes) -> str:
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()


def format_bytes(n):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if n < 1024:
            return f'{n:.1f} {unit}'
        n /= 1024
    return f'{n:.1f} TB'


def xor_bytes(a: bytes, b: bytes) -> bytes:
    if len(a) != len(b):
        size = max(len(a), len(b))
        a = a.ljust(size, b'\x00')
        b = b.ljust(size, b'\x00')
    return bytes(x ^ y for x, y in zip(a, b))


def retry_request(fn, retries=3, backoff=0.5):
    last_exc = None
    for attempt in range(retries):
        try:
            resp = fn()
            if resp.status_code < 500:
                return resp
        except Exception as e:
            last_exc = e
        if attempt < retries - 1:
            time.sleep(backoff * (2 ** attempt))
    if last_exc:
        raise last_exc
    return resp


# ─── Node Health ──────────────────────────────────────────────────────────────

def get_healthy_nodes():
    healthy = []
    for name, url in NODES.items():
        try:
            r = requests.get(f'{url}/health', timeout=1.5)
            if r.status_code == 200:
                healthy.append((name, url))
        except Exception:
            pass
    return healthy


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
        auth_status = requests.get(f'{AUTH_URL}/health', timeout=2).json()
    except Exception:
        auth_status = {'status': 'down'}

    try:
        meta_status = requests.get(f'{META_URL}/health', timeout=2).json()
    except Exception:
        meta_status = {'status': 'down'}

    return jsonify({
        'orchestrator': {'status': 'ok', 'service': 'orchestrator'},
        'auth_service':  auth_status,
        'metadata_db':   meta_status,
        'nodes':         statuses,
    })


# ─── Routes: Upload (parallel + XOR parity) ──────────────────────────────────

@app.route('/upload', methods=['POST'])
def upload_file():
    user, err = require_auth(request)
    if err:
        return err

    UPLOAD_TOTAL.inc()
    upload_start = time.time()

    if 'file' not in request.files:
        UPLOAD_FAILURE.inc()
        return jsonify({'error': 'No file provided'}), 400

    f            = request.files['file']
    filename     = f.filename or 'unnamed'
    content_type = f.content_type or 'application/octet-stream'
    raw_bytes    = f.read()
    original_size = len(raw_bytes)

    print(f'[UPLOAD] "{filename}" | {format_bytes(original_size)} | {content_type} | owner={user["username"]}')

    healthy = get_healthy_nodes()
    if len(healthy) < 3:
        UPLOAD_FAILURE.inc()
        node_names = [n for n, _ in healthy]
        return jsonify({
            'error': f'Need 3 healthy nodes, only {len(healthy)} available: {node_names}'
        }), 503

    # ── XOR Parity sharding ──────────────────────────────────────────────────
    L  = len(raw_bytes)
    c1 = L // 3
    c2 = 2 * (L // 3)
    chunk0 = raw_bytes[:c1]
    chunk1 = raw_bytes[c1:c2]
    chunk2 = raw_bytes[c2:]

    max_len = max(len(chunk0), len(chunk1), len(chunk2))
    chunk0_p = chunk0.ljust(max_len, b'\x00')
    chunk1_p = chunk1.ljust(max_len, b'\x00')
    chunk2_p = chunk2.ljust(max_len, b'\x00')

    parity_bytes = xor_bytes(xor_bytes(chunk0_p, chunk1_p), chunk2_p)

    parts = [
        base64.b64encode(chunk0).decode('utf-8'),
        base64.b64encode(chunk1).decode('utf-8'),
        base64.b64encode(chunk2).decode('utf-8'),
        base64.b64encode(parity_bytes).decode('utf-8'),
    ]
    chunk_sizes = [len(chunk0), len(chunk1), len(chunk2)]
    hashes      = [sha256(chunk0), sha256(chunk1), sha256(chunk2)]
    parity_hash = sha256(parity_bytes)

    file_id   = str(uuid.uuid4())
    shard_ids = [f'{file_id}_shard_{i}' for i in range(3)]
    parity_id = f'{file_id}_parity'

    node_list = list(NODES.items())
    hdrs      = auth_headers()

    print(f'[FILE_ID] {file_id} | Chunks: {chunk_sizes} bytes')

    upload_tasks = [
        (shard_ids[0], parts[0], node_list[0][0], node_list[0][1], 0, False),
        (shard_ids[1], parts[1], node_list[1][0], node_list[1][1], 1, False),
        (shard_ids[2], parts[2], node_list[2][0], node_list[2][1], 2, False),
        (parity_id,    parts[3], node_list[2][0], node_list[2][1], None, True),
    ]

    successfully_written = []

    def store_single(task):
        sid, data, node_name, node_url, idx, is_parity = task
        label = 'PARITY' if is_parity else f'SHARD {idx}'
        try:
            resp = retry_request(
                lambda: requests.post(
                    f'{node_url}/shards',
                    json={'shard_id': sid, 'data': data},
                    headers=hdrs,
                    timeout=15
                )
            )
            if resp.status_code == 200:
                print(f'[{label}] → {node_name} OK')
                return sid, node_url, None
            else:
                err = f'{label} on {node_name}: HTTP {resp.status_code}'
                print(f'[ERROR] {err}')
                return sid, node_url, err
        except Exception as e:
            err = f'{label} → {node_name} FAILED: {e}'
            print(f'[ERROR] {err}')
            return sid, node_url, err

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(store_single, upload_tasks))

    store_errors  = [(sid, url, err) for sid, url, err in results if err is not None]
    written_shards = [(sid, url) for sid, url, err in results if err is None]

    if store_errors:
        error_descriptions = [err for _, _, err in store_errors]
        print(f'[ROLLBACK] {len(store_errors)} shard error(s) — rolling back')

        def delete_shard(sid_url):
            sid, node_url = sid_url
            try:
                requests.delete(f'{node_url}/shards/{sid}', headers=hdrs, timeout=5)
            except Exception:
                pass

        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(delete_shard, written_shards))

        UPLOAD_FAILURE.inc()
        UPLOAD_LATENCY.observe(time.time() - upload_start)
        return jsonify({
            'error':   f'Upload aborted — {len(store_errors)} shard(s) failed to write',
            'details': error_descriptions,
            'status':  'rolled_back',
        }), 503

    metadata = {
        'file_id':       file_id,
        'filename':      filename,
        'original_size': original_size,
        'content_type':  content_type,
        'shard_count':   3,
        'chunk_sizes':   chunk_sizes,
        'owner_id':      user['user_id'],   # ← NEW: save owner
        'shards': [
            {'shard_id': shard_ids[0], 'shard_index': 0,
             'node_url': node_list[0][1], 'node_name': node_list[0][0],
             'hash': hashes[0], 'is_parity': False},
            {'shard_id': shard_ids[1], 'shard_index': 1,
             'node_url': node_list[1][1], 'node_name': node_list[1][0],
             'hash': hashes[1], 'is_parity': False},
            {'shard_id': shard_ids[2], 'shard_index': 2,
             'node_url': node_list[2][1], 'node_name': node_list[2][0],
             'hash': hashes[2], 'is_parity': False},
            {'shard_id': parity_id, 'shard_index': -1,
             'node_url': node_list[2][1], 'node_name': node_list[2][0],
             'hash': parity_hash, 'is_parity': True, 'parity_for_index': -1},
        ]
    }

    try:
        retry_request(lambda: requests.post(f'{META_URL}/files', json=metadata, timeout=10))
        print(f'[META] Recipe saved OK (owner={user["username"]})')
    except Exception as e:
        for sid, node_url in written_shards:
            try:
                requests.delete(f'{node_url}/shards/{sid}', headers=hdrs, timeout=5)
            except Exception:
                pass
        UPLOAD_FAILURE.inc()
        UPLOAD_LATENCY.observe(time.time() - upload_start)
        return jsonify({'error': f'Metadata save failed after shard writes — rolled back: {e}'}), 500

    UPLOAD_SUCCESS.inc()
    UPLOAD_LATENCY.observe(time.time() - upload_start)
    print(f'[DONE] Upload complete in {time.time() - upload_start:.2f}s')
    return jsonify({
        'status':              'uploaded',
        'file_id':             file_id,
        'filename':            filename,
        'original_size':       original_size,
        'original_size_human': format_bytes(original_size),
        'shard_hashes':        hashes,
        'parity_hash':         parity_hash,
        'errors':              [],
        'parity_node':         node_list[2][0],
    })


# ─── Routes: List files ──────────────────────────────────────────────────────

@app.route('/files', methods=['GET'])
def list_files():
    user, err = require_auth(request)
    if err:
        return err
    try:
        if user['role'] == 'admin':
            # Admin sees ALL files
            r = requests.get(f'{META_URL}/files', timeout=5)
        else:
            # Regular user sees only their own files
            r = requests.get(f'{META_URL}/files?owner={user["user_id"]}', timeout=5)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ─── Routes: Shard Explorer ──────────────────────────────────────────────────

@app.route('/files/<file_id>/peek', methods=['GET'])
def peek_shards(file_id):
    user, err = require_auth(request)
    if err:
        return err

    try:
        r = requests.get(f'{META_URL}/files/{file_id}', timeout=5)
        if r.status_code == 404:
            return jsonify({'error': 'File not found'}), 404
        meta = r.json()
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    # Ownership check
    if user['role'] != 'admin' and meta.get('owner_id') != user['user_id']:
        return jsonify({'error': 'Forbidden'}), 403

    hdrs   = auth_headers()
    result = []
    for shard in meta['shards']:
        info = {
            'shard_id':         shard['shard_id'],
            'shard_index':      shard['shard_index'],
            'node_name':        shard['node_name'],
            'node_url':         shard['node_url'],
            'hash':             shard['hash'],
            'is_parity':        shard['is_parity'],
            'parity_for_index': shard.get('parity_for_index'),
            'preview':          None,
            'stored_size':      0,
            'status':           'unknown',
        }
        try:
            r = requests.get(
                f'{shard["node_url"]}/shards/{shard["shard_id"]}',
                headers=hdrs, timeout=3
            )
            if r.status_code == 200:
                data = r.json()['data']
                info['preview']     = data[:120]
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


# ─── Routes: Download (fault-tolerant XOR recovery) ─────────────────────────

@app.route('/download/<file_id>', methods=['GET'])
def download_file(file_id):
    user, err = require_auth(request)
    if err:
        return err

    DOWNLOAD_TOTAL.inc()
    download_start = time.time()

    try:
        r = requests.get(f'{META_URL}/files/{file_id}', timeout=5)
        if r.status_code == 404:
            DOWNLOAD_FAILURE.inc()
            return jsonify({'error': 'File not found'}), 404
        meta = r.json()
    except Exception as e:
        DOWNLOAD_FAILURE.inc()
        return jsonify({'error': f'Metadata unreachable: {e}'}), 500

    # Ownership check
    if user['role'] != 'admin' and meta.get('owner_id') != user['user_id']:
        DOWNLOAD_FAILURE.inc()
        return jsonify({'error': 'Forbidden — you do not own this file'}), 403

    shards_info    = meta['shards']
    primary_shards = sorted([s for s in shards_info if not s['is_parity']], key=lambda x: x['shard_index'])
    parity_shard   = next((s for s in shards_info if s['is_parity']), None)
    chunk_sizes    = meta.get('chunk_sizes', [None, None, None])

    hdrs      = auth_headers()
    log       = []
    corrupted = []
    recovered = []

    def fetch_shard(shard):
        try:
            resp = retry_request(
                lambda: requests.get(
                    f'{shard["node_url"]}/shards/{shard["shard_id"]}',
                    headers=hdrs, timeout=5
                )
            )
            if resp.status_code == 200:
                return shard['shard_index'], resp.json()['data'], shard['node_name'], None
        except Exception as e:
            return shard['shard_index'], None, shard['node_name'], str(e)
        return shard['shard_index'], None, shard['node_name'], f'HTTP {resp.status_code}'

    raw_b64_parts = {}
    with ThreadPoolExecutor(max_workers=3) as exec:
        futures = {exec.submit(fetch_shard, s): s for s in primary_shards}
        for future in as_completed(futures):
            idx, b64data, node_name, err = future.result()
            if b64data is not None:
                raw_b64_parts[idx] = b64data
                log.append(f'[OK] Shard {idx} fetched from {node_name}')
            else:
                log.append(f'[WARN] Shard {idx} unavailable from {node_name}: {err}')

    for shard in primary_shards:
        idx = shard['shard_index']
        if idx not in raw_b64_parts:
            continue
        b64data = raw_b64_parts[idx]
        try:
            decoded_bytes = base64.b64decode(b64data)
            actual_hash   = sha256(decoded_bytes)
        except Exception as decode_err:
            log.append(f'[CORRUPT] Shard {idx} base64 decode error: {decode_err}')
            corrupted.append(idx)
            del raw_b64_parts[idx]
            continue
        if actual_hash != shard['hash']:
            corrupted.append(idx)
            del raw_b64_parts[idx]
            log.append(f'[CORRUPT] Shard {idx} checksum MISMATCH — treating as missing')
        else:
            log.append(f'[INTEGRITY] Shard {idx} verified OK')

    missing = [s['shard_index'] for s in primary_shards if s['shard_index'] not in raw_b64_parts]

    if len(missing) == 1 and parity_shard:
        missing_idx = missing[0]
        log.append(f'[RECOVERY] Shard {missing_idx} missing — attempting XOR recovery')
        try:
            par_resp = retry_request(
                lambda: requests.get(
                    f'{parity_shard["node_url"]}/shards/{parity_shard["shard_id"]}',
                    headers=hdrs, timeout=5
                )
            )
            if par_resp.status_code == 200:
                parity_b64 = par_resp.json()['data']
                parity_raw = base64.b64decode(parity_b64)

                known_indices = [i for i in range(3) if i != missing_idx]
                recovered_raw = parity_raw
                for ki in known_indices:
                    known_raw = base64.b64decode(raw_b64_parts[ki])
                    padded    = known_raw.ljust(len(recovered_raw), b'\x00')
                    recovered_raw = xor_bytes(recovered_raw, padded)

                original_chunk_size = chunk_sizes[missing_idx]
                if original_chunk_size is not None:
                    recovered_raw = recovered_raw[:original_chunk_size]

                recovered_hash = sha256(recovered_raw)
                expected_hash  = primary_shards[missing_idx]['hash']
                if recovered_hash != expected_hash:
                    log.append(f'[RECOVERY] XOR recovery produced bad hash for shard {missing_idx}')
                    DOWNLOAD_FAILURE.inc()
                    DOWNLOAD_LATENCY.observe(time.time() - download_start)
                    return jsonify({'error': f'IRRECOVERABLE — Shard {missing_idx} XOR recovery failed', 'log': log}), 503

                raw_b64_parts[missing_idx] = base64.b64encode(recovered_raw).decode('utf-8')
                recovered.append(missing_idx)
                DOWNLOAD_RECOVERY.inc()
                log.append(f'[RECOVERY] Shard {missing_idx} XOR-recovered successfully ✓')
            else:
                log.append(f'[ERROR] Parity node returned HTTP {par_resp.status_code}')
        except Exception as e:
            log.append(f'[ERROR] Parity fetch failed: {e}')

    elif len(missing) > 1:
        DOWNLOAD_FAILURE.inc()
        DOWNLOAD_LATENCY.observe(time.time() - download_start)
        return jsonify({
            'error':           f'IRRECOVERABLE — {len(missing)} shards missing',
            'missing_indices': missing,
            'log':             log,
        }), 503

    if len(missing) == 1 and missing[0] not in recovered:
        DOWNLOAD_FAILURE.inc()
        DOWNLOAD_LATENCY.observe(time.time() - download_start)
        return jsonify({'error': f'IRRECOVERABLE — Shard {missing[0]} lost and parity unavailable', 'log': log}), 503

    try:
        file_bytes = b''
        for i in range(3):
            file_bytes += base64.b64decode(raw_b64_parts[i])
    except Exception as decode_err:
        DOWNLOAD_FAILURE.inc()
        DOWNLOAD_LATENCY.observe(time.time() - download_start)
        return jsonify({'filename': meta['filename'], 'log': log, 'corrupted_shards': corrupted,
                        'error': f'Reconstruction failed — {decode_err}'}), 422

    DOWNLOAD_LATENCY.observe(time.time() - download_start)
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
    user, err = require_auth(request)
    if err:
        return err

    try:
        r = requests.get(f'{META_URL}/files/{file_id}', timeout=5)
        meta = r.json()
    except Exception:
        return jsonify({'error': 'File not found'}), 404

    # Ownership check
    if user['role'] != 'admin' and meta.get('owner_id') != user['user_id']:
        return jsonify({'error': 'Forbidden — you do not own this file'}), 403

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


# ─── Routes: Demo ────────────────────────────────────────────────────────────

@app.route('/demo/corrupt/<file_id>/<int:shard_index>', methods=['POST'])
def corrupt_demo(file_id, shard_index):
    user, err = require_auth(request)
    if err:
        return err

    try:
        r = requests.get(f'{META_URL}/files/{file_id}', timeout=5)
        meta = r.json()
    except Exception:
        return jsonify({'error': 'File not found'}), 404

    if user['role'] != 'admin' and meta.get('owner_id') != user['user_id']:
        return jsonify({'error': 'Forbidden'}), 403

    hdrs   = auth_headers()
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


# ─── Nodes: Dynamic listing ──────────────────────────────────────────────────

@app.route('/nodes', methods=['GET'])
def list_nodes():
    result = {}
    for name, url in NODES.items():
        try:
            r = requests.get(f'{url}/health', timeout=2)
            result[name] = {'url': url, **r.json()}
        except Exception as e:
            result[name] = {'url': url, 'status': 'down', 'error': str(e)}
    return jsonify(result)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
