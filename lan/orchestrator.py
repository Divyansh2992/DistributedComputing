"""
ShardVault LAN — Orchestrator
==============================
Laptop 1 (MASTER). Run with:
  python orchestrator.py

Serves:
  - POST   /upload
  - GET    /download/<file_id>
  - GET    /files
  - GET    /files/<file_id>
  - DELETE /files/<file_id>
  - GET    /files/<file_id>/peek
  - POST   /demo/corrupt/<file_id>/<shard_index>
  - GET    /health
  - GET    /nodes
  - GET    /metrics

All node URLs come from config.py (or environment variables).
Edit config.py before starting.
"""

import os
import sys
import uuid
import base64
import hashlib
import time
import threading
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST

# ─── Load config ─────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))
import config

app = Flask(__name__, static_folder='static')
CORS(app)

JWT_SECRET = config.JWT_SECRET
AUTH_URL   = config.AUTH_URL
META_URL   = config.META_URL
NODES      = dict(config.NODES)   # mutable copy: {name: url}

# ─── Prometheus Metrics ───────────────────────────────────────────────────────
UPLOAD_TOTAL      = Counter('shardvault_uploads_total',            'Total uploads attempted')
UPLOAD_SUCCESS    = Counter('shardvault_uploads_success',          'Uploads completed OK')
UPLOAD_FAILURE    = Counter('shardvault_uploads_failed',           'Uploads that failed/rolled back')
DOWNLOAD_TOTAL    = Counter('shardvault_downloads_total',          'Total download requests')
DOWNLOAD_RECOVERY = Counter('shardvault_downloads_xor_recovered',  'Downloads needing XOR recovery')
DOWNLOAD_FAILURE  = Counter('shardvault_downloads_failed',         'Downloads that could not be served')
UPLOAD_LATENCY    = Histogram('shardvault_upload_duration_seconds',   'Upload latency',   buckets=[.05,.1,.25,.5,1,2,5,10])
DOWNLOAD_LATENCY  = Histogram('shardvault_download_duration_seconds', 'Download latency', buckets=[.05,.1,.25,.5,1,2,5,10])
FILES_STORED      = Gauge('shardvault_files_stored_total', 'Files in metadata DB')


@app.route('/metrics')
def metrics_endpoint():
    try:
        r = requests.get(f'{META_URL}/files', timeout=3)
        FILES_STORED.set(len(r.json()))
    except Exception:
        pass
    return generate_latest(), 200, {'Content-Type': CONTENT_TYPE_LATEST}


# ─── Token Cache ─────────────────────────────────────────────────────────────
_token_lock   = threading.Lock()
_cached_token = None
_token_expiry = 0


def get_token(force_refresh=False):
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


# ─── Utilities ───────────────────────────────────────────────────────────────

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
    """Retry fn() with exponential backoff. Returns Response or raises."""
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


def get_healthy_nodes():
    """Return list of (name, url) for currently responding nodes."""
    healthy = []
    for name, url in NODES.items():
        try:
            r = requests.get(f'{url}/health', timeout=2)
            if r.status_code == 200:
                healthy.append((name, url))
        except Exception:
            pass
    return healthy


# ─── Static / UI ─────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')


# ─── Health ──────────────────────────────────────────────────────────────────

@app.route('/health', methods=['GET'])
def health():
    node_statuses = {}
    for name, url in NODES.items():
        try:
            r = requests.get(f'{url}/health', timeout=2)
            node_statuses[name] = {'url': url, **r.json()}
        except Exception:
            node_statuses[name] = {'url': url, 'status': 'down', 'node': name}

    try:
        auth_status = requests.get(f'{AUTH_URL}/health', timeout=2).json()
    except Exception:
        auth_status = {'status': 'down', 'service': 'auth_service'}

    try:
        meta_status = requests.get(f'{META_URL}/health', timeout=2).json()
    except Exception:
        meta_status = {'status': 'down', 'service': 'metadata_db'}

    return jsonify({
        'orchestrator': {'status': 'ok', 'service': 'orchestrator'},
        'auth_service':  auth_status,
        'metadata_db':   meta_status,
        'nodes':         node_statuses,
    })


# ─── Node list (live) ─────────────────────────────────────────────────────────

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


# ─── Upload ──────────────────────────────────────────────────────────────────

@app.route('/upload', methods=['POST'])
def upload_file():
    UPLOAD_TOTAL.inc()
    t0 = time.time()

    if 'file' not in request.files:
        UPLOAD_FAILURE.inc()
        return jsonify({'error': 'No file provided'}), 400

    f             = request.files['file']
    filename      = f.filename or 'unnamed'
    content_type  = f.content_type or 'application/octet-stream'
    raw_bytes     = f.read()
    original_size = len(raw_bytes)

    print(f'[UPLOAD] "{filename}" | {format_bytes(original_size)}')

    # Need all 3 nodes healthy before committing
    healthy = get_healthy_nodes()
    if len(healthy) < 3:
        UPLOAD_FAILURE.inc()
        return jsonify({
            'error': f'Need 3 healthy nodes, only {len(healthy)} available: {[n for n,_ in healthy]}'
        }), 503

    # ── XOR Sharding ─────────────────────────────────────────────────────────
    L  = len(raw_bytes)
    c1 = L // 3
    c2 = 2 * (L // 3)
    chunk0 = raw_bytes[:c1]
    chunk1 = raw_bytes[c1:c2]
    chunk2 = raw_bytes[c2:]

    mx  = max(len(chunk0), len(chunk1), len(chunk2))
    p0  = chunk0.ljust(mx, b'\x00')
    p1  = chunk1.ljust(mx, b'\x00')
    p2  = chunk2.ljust(mx, b'\x00')
    parity_bytes = xor_bytes(xor_bytes(p0, p1), p2)

    parts = [
        base64.b64encode(chunk0).decode('utf-8'),
        base64.b64encode(chunk1).decode('utf-8'),
        base64.b64encode(chunk2).decode('utf-8'),
        base64.b64encode(parity_bytes).decode('utf-8'),
    ]
    chunk_sizes = [len(chunk0), len(chunk1), len(chunk2)]
    hashes      = [sha256(chunk0), sha256(chunk1), sha256(chunk2)]
    parity_hash = sha256(parity_bytes)

    file_id    = str(uuid.uuid4())
    shard_ids  = [f'{file_id}_shard_{i}' for i in range(3)]
    parity_id  = f'{file_id}_parity'

    node_list = list(NODES.items())   # [ (name, url), ... ]
    hdrs      = auth_headers()

    print(f'[FILE_ID] {file_id} | Chunks: {chunk_sizes} bytes')

    # Parity layout: shard_0→node_0, shard_1→node_1, shard_2→node_2, parity→node_2
    upload_tasks = [
        (shard_ids[0], parts[0], node_list[0][0], node_list[0][1], 0,    False),
        (shard_ids[1], parts[1], node_list[1][0], node_list[1][1], 1,    False),
        (shard_ids[2], parts[2], node_list[2][0], node_list[2][1], 2,    False),
        (parity_id,    parts[3], node_list[2][0], node_list[2][1], None, True),
    ]

    successfully_written = []

    def store_shard(task):
        sid, data, node_name, node_url, idx, is_parity = task
        label = 'PARITY' if is_parity else f'SHARD {idx}'
        try:
            resp = retry_request(lambda: requests.post(
                f'{node_url}/shards',
                json={'shard_id': sid, 'data': data},
                headers=hdrs,
                timeout=30
            ))
            if resp.status_code == 200:
                print(f'[{label}] → {node_name} OK')
                return sid, node_url, None
            return sid, node_url, f'{label} HTTP {resp.status_code}'
        except Exception as e:
            return sid, node_url, f'{label} FAILED: {e}'

    with ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(store_shard, upload_tasks))

    errors        = [(s, u, e) for s, u, e in results if e]
    written_shards = [(s, u)   for s, u, e in results if not e]

    # Atomic rollback if any write failed
    if errors:
        errs = [e for _, _, e in errors]
        print(f'[ROLLBACK] {len(errors)} error(s) — rolling back {len(written_shards)} write(s)')

        def delete_shard(su):
            sid, url = su
            try:
                requests.delete(f'{url}/shards/{sid}', headers=hdrs, timeout=5)
            except Exception:
                pass

        with ThreadPoolExecutor(max_workers=4) as ex:
            list(ex.map(delete_shard, written_shards))

        UPLOAD_FAILURE.inc()
        UPLOAD_LATENCY.observe(time.time() - t0)
        return jsonify({'error': f'Upload aborted — {len(errors)} shard(s) failed', 'details': errs, 'status': 'rolled_back'}), 503

    # Save metadata
    metadata = {
        'file_id':       file_id,
        'filename':      filename,
        'original_size': original_size,
        'content_type':  content_type,
        'shard_count':   3,
        'chunk_sizes':   chunk_sizes,
        'shards': [
            {'shard_id': shard_ids[0], 'shard_index': 0,  'node_url': node_list[0][1], 'node_name': node_list[0][0], 'hash': hashes[0], 'is_parity': False},
            {'shard_id': shard_ids[1], 'shard_index': 1,  'node_url': node_list[1][1], 'node_name': node_list[1][0], 'hash': hashes[1], 'is_parity': False},
            {'shard_id': shard_ids[2], 'shard_index': 2,  'node_url': node_list[2][1], 'node_name': node_list[2][0], 'hash': hashes[2], 'is_parity': False},
            {'shard_id': parity_id,    'shard_index': -1, 'node_url': node_list[2][1], 'node_name': node_list[2][0], 'hash': parity_hash, 'is_parity': True, 'parity_for_index': -1},
        ]
    }

    try:
        retry_request(lambda: requests.post(f'{META_URL}/files', json=metadata, timeout=10))
    except Exception as e:
        print(f'[ROLLBACK] Metadata save failed: {e}')
        for sid, url in written_shards:
            try:
                requests.delete(f'{url}/shards/{sid}', headers=hdrs, timeout=5)
            except Exception:
                pass
        UPLOAD_FAILURE.inc()
        UPLOAD_LATENCY.observe(time.time() - t0)
        return jsonify({'error': f'Metadata save failed — rolled back: {e}'}), 500

    UPLOAD_SUCCESS.inc()
    UPLOAD_LATENCY.observe(time.time() - t0)
    print(f'[DONE] Upload complete in {time.time()-t0:.2f}s')

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


# ─── Files List ──────────────────────────────────────────────────────────────

@app.route('/files', methods=['GET'])
def list_files():
    try:
        r = requests.get(f'{META_URL}/files', timeout=5)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ─── Shard Peek ──────────────────────────────────────────────────────────────

@app.route('/files/<file_id>/peek', methods=['GET'])
def peek_shards(file_id):
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
            'shard_id':    shard['shard_id'],
            'shard_index': shard['shard_index'],
            'node_name':   shard['node_name'],
            'node_url':    shard['node_url'],
            'hash':        shard['hash'],
            'is_parity':   shard['is_parity'],
            'status':      'unknown',
            'stored_size': 0,
            'preview':     None,
        }
        try:
            resp = requests.get(f'{shard["node_url"]}/shards/{shard["shard_id"]}', headers=hdrs, timeout=4)
            if resp.status_code == 200:
                data = resp.json()['data']
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


# ─── Download ────────────────────────────────────────────────────────────────

@app.route('/download/<file_id>', methods=['GET'])
def download_file(file_id):
    DOWNLOAD_TOTAL.inc()
    t0 = time.time()

    try:
        r = requests.get(f'{META_URL}/files/{file_id}', timeout=5)
        if r.status_code == 404:
            DOWNLOAD_FAILURE.inc()
            return jsonify({'error': 'File not found'}), 404
        meta = r.json()
    except Exception as e:
        DOWNLOAD_FAILURE.inc()
        return jsonify({'error': f'Metadata unreachable: {e}'}), 500

    primary  = sorted([s for s in meta['shards'] if not s['is_parity']], key=lambda x: x['shard_index'])
    parity   = next((s for s in meta['shards'] if s['is_parity']), None)
    chunks   = meta.get('chunk_sizes', [None, None, None])
    hdrs     = auth_headers()
    log      = []
    corrupted = []
    recovered = []

    # Parallel fetch of primary shards
    def fetch(shard):
        try:
            resp = retry_request(lambda: requests.get(
                f'{shard["node_url"]}/shards/{shard["shard_id"]}',
                headers=hdrs, timeout=10
            ))
            if resp.status_code == 200:
                return shard['shard_index'], resp.json()['data'], shard['node_name'], None
        except Exception as e:
            return shard['shard_index'], None, shard['node_name'], str(e)
        return shard['shard_index'], None, shard['node_name'], f'HTTP {resp.status_code}'

    raw_b64 = {}
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(fetch, s): s for s in primary}
        for fut in as_completed(futs):
            idx, b64data, node_name, err = fut.result()
            if b64data is not None:
                raw_b64[idx] = b64data
                log.append(f'[OK] Shard {idx} from {node_name}')
            else:
                log.append(f'[WARN] Shard {idx} unavailable from {node_name}: {err}')

    # SHA-256 integrity check
    for shard in primary:
        idx = shard['shard_index']
        if idx not in raw_b64:
            continue
        try:
            decoded   = base64.b64decode(raw_b64[idx])
            real_hash = sha256(decoded)
        except Exception as de:
            log.append(f'[CORRUPT] Shard {idx} bad base64: {de}')
            corrupted.append(idx)
            del raw_b64[idx]
            continue
        if real_hash != shard['hash']:
            corrupted.append(idx)
            del raw_b64[idx]
            log.append(f'[CORRUPT] Shard {idx} SHA-256 MISMATCH — treating as missing')
        else:
            log.append(f'[INTEGRITY] Shard {idx} OK')

    missing = [s['shard_index'] for s in primary if s['shard_index'] not in raw_b64]

    # XOR recovery if exactly 1 shard is missing/corrupted
    if len(missing) == 1 and parity:
        mi = missing[0]
        log.append(f'[RECOVERY] Attempting XOR recovery of shard {mi}')
        try:
            pr = retry_request(lambda: requests.get(
                f'{parity["node_url"]}/shards/{parity["shard_id"]}',
                headers=hdrs, timeout=10
            ))
            if pr.status_code == 200:
                parity_raw = base64.b64decode(pr.json()['data'])
                known      = [i for i in range(3) if i != mi]
                rec_raw    = parity_raw
                for ki in known:
                    kr  = base64.b64decode(raw_b64[ki])
                    pad = kr.ljust(len(rec_raw), b'\x00')
                    rec_raw = xor_bytes(rec_raw, pad)

                csz = chunks[mi]
                if csz is not None:
                    rec_raw = rec_raw[:csz]

                if sha256(rec_raw) != primary[mi]['hash']:
                    DOWNLOAD_FAILURE.inc()
                    DOWNLOAD_LATENCY.observe(time.time() - t0)
                    return jsonify({'error': f'IRRECOVERABLE — XOR hash mismatch for shard {mi}', 'log': log}), 503

                raw_b64[mi] = base64.b64encode(rec_raw).decode('utf-8')
                recovered.append(mi)
                DOWNLOAD_RECOVERY.inc()
                log.append(f'[RECOVERY] Shard {mi} XOR-recovered ✓')
            else:
                log.append(f'[ERROR] Parity node HTTP {pr.status_code}')
        except Exception as e:
            log.append(f'[ERROR] Parity fetch failed: {e}')

    elif len(missing) > 1:
        DOWNLOAD_FAILURE.inc()
        DOWNLOAD_LATENCY.observe(time.time() - t0)
        return jsonify({'error': f'IRRECOVERABLE — {len(missing)} shards missing', 'missing_indices': missing, 'log': log}), 503

    if len(missing) == 1 and missing[0] not in recovered:
        DOWNLOAD_FAILURE.inc()
        DOWNLOAD_LATENCY.observe(time.time() - t0)
        return jsonify({'error': f'IRRECOVERABLE — Shard {missing[0]} lost and parity unavailable', 'log': log}), 503

    try:
        file_bytes = b''
        for i in range(3):
            file_bytes += base64.b64decode(raw_b64[i])
    except Exception as de:
        DOWNLOAD_FAILURE.inc()
        DOWNLOAD_LATENCY.observe(time.time() - t0)
        return jsonify({'error': f'Reconstruction failed: {de}', 'log': log}), 422

    DOWNLOAD_LATENCY.observe(time.time() - t0)
    return jsonify({
        'filename':         meta['filename'],
        'content_type':     meta['content_type'],
        'size':             len(file_bytes),
        'log':              log,
        'corrupted_shards': corrupted,
        'recovered_shards': recovered,
        'data_b64':         base64.b64encode(file_bytes).decode('utf-8'),
    })


# ─── Delete ──────────────────────────────────────────────────────────────────

@app.route('/files/<file_id>', methods=['DELETE'])
def delete_file(file_id):
    try:
        r    = requests.get(f'{META_URL}/files/{file_id}', timeout=5)
        meta = r.json()
    except Exception:
        return jsonify({'error': 'File not found'}), 404

    hdrs = auth_headers()
    for shard in meta.get('shards', []):
        try:
            requests.delete(f'{shard["node_url"]}/shards/{shard["shard_id"]}', headers=hdrs, timeout=3)
        except Exception:
            pass
    try:
        requests.delete(f'{META_URL}/files/{file_id}', timeout=5)
    except Exception:
        pass
    return jsonify({'status': 'deleted', 'file_id': file_id})


# ─── Demo: corrupt shard ─────────────────────────────────────────────────────

@app.route('/demo/corrupt/<file_id>/<int:shard_index>', methods=['POST'])
def corrupt_demo(file_id, shard_index):
    try:
        r    = requests.get(f'{META_URL}/files/{file_id}', timeout=5)
        meta = r.json()
    except Exception:
        return jsonify({'error': 'File not found'}), 404

    hdrs   = auth_headers()
    target = next((s for s in meta['shards'] if not s['is_parity'] and s['shard_index'] == shard_index), None)
    if not target:
        return jsonify({'error': f'Shard {shard_index} not found'}), 404

    try:
        requests.post(f'{target["node_url"]}/shards/{target["shard_id"]}/corrupt', headers=hdrs, timeout=5)
        return jsonify({'status': 'shard_corrupted', 'shard_index': shard_index, 'node': target['node_name']})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ─── Run ─────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    port = config.ORCHESTRATOR_PORT
    print(f"""
╔══════════════════════════════════════════════════════╗
║  ShardVault Orchestrator  —  LAN Mode                ║
╠══════════════════════════════════════════════════════╣
║  Auth URL  : {config.AUTH_URL:<39} ║
║  Meta URL  : {config.META_URL:<39} ║
╠══════════════════════════════════════════════════════╣""")
    for name, url in config.NODES.items():
        print(f'║  {name:<12}: {url:<39} ║')
    print(f"""╠══════════════════════════════════════════════════════╣
║  UI + API  : http://0.0.0.0:{port}                      ║
╚══════════════════════════════════════════════════════╝
""")
    app.run(host='0.0.0.0', port=port, debug=False)
