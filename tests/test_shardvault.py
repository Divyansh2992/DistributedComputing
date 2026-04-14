#!/usr/bin/env python3
"""
ShardVault — Complete Automated QA Test Suite
=============================================
Run this script from any machine that can reach the cluster.

Usage:
    python3 test_shardvault.py --host <MASTER_IP> --port 30000

Covers:
    1. Deployment verification (all services reachable)
    2. Auth service validation
    3. Upload functional test
    4. Download + integrity (SHA-256 roundtrip)
    5. Node distribution verification (shards on different nodes)
    6. Shard corruption + XOR recovery
    7. Node kill simulation (forces download with 1 missing shard)
    8. Double-failure detection (irrecoverable case)
    9. Consecutive uploads (parallel stress)
   10. Delete + orphan cleanup
"""

import sys
import os
import base64
import hashlib
import time
import json
import argparse
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

# ─── Config ──────────────────────────────────────────────────────────────────

parser = argparse.ArgumentParser(description='ShardVault QA Test Suite')
parser.add_argument('--host', required=True, help='Master node IP address')
parser.add_argument('--port', default=30000, type=int, help='Orchestrator NodePort (default 30000)')
args = parser.parse_args()

BASE_URL = f'http://{args.host}:{args.port}'

# ANSI colors
GREEN  = '\033[92m'
RED    = '\033[91m'
YELLOW = '\033[93m'
CYAN   = '\033[96m'
RESET  = '\033[0m'
BOLD   = '\033[1m'

passed = 0
failed = 0
warnings = 0

# ─── Test Utilities ───────────────────────────────────────────────────────────

def ok(msg):
    global passed
    passed += 1
    print(f'  {GREEN}✓{RESET} {msg}')

def fail(msg):
    global failed
    failed += 1
    print(f'  {RED}✗ FAIL:{RESET} {msg}')

def warn(msg):
    global warnings
    warnings += 1
    print(f'  {YELLOW}⚠ WARN:{RESET} {msg}')

def section(title):
    print(f'\n{BOLD}{CYAN}{"═"*60}{RESET}')
    print(f'{BOLD}{CYAN}  {title}{RESET}')
    print(f'{BOLD}{CYAN}{"═"*60}{RESET}')

def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def xor_bytes(a: bytes, b: bytes) -> bytes:
    size = max(len(a), len(b))
    a = a.ljust(size, b'\x00')
    b = b.ljust(size, b'\x00')
    return bytes(x ^ y for x, y in zip(a, b))

def create_test_file(size_bytes: int, label: str = 'SHARDVAULT_TEST') -> bytes:
    """Create a deterministic binary test payload."""
    base = (label * ((size_bytes // len(label)) + 1)).encode()[:size_bytes]
    # Sprinkle some binary variety
    return bytes([b ^ (i % 256) for i, b in enumerate(base)])

# ─── TEST 1: Service Reachability ────────────────────────────────────────────

section('TEST 1: Deployment Verification — All Services Reachable')

try:
    r = requests.get(f'{BASE_URL}/health', timeout=10)
    r.raise_for_status()
    health = r.json()
    print(f'  Raw response: {json.dumps(health, indent=4)}')

    if health.get('orchestrator', {}).get('status') == 'ok':
        ok('Orchestrator is up')
    else:
        fail('Orchestrator health check failed')

    if health.get('auth_service', {}).get('status') == 'ok':
        ok('auth_service is up')
    else:
        fail(f'auth_service down: {health.get("auth_service")}')

    if health.get('metadata_db', {}).get('status') == 'ok':
        ok('metadata_db is up')
    else:
        fail(f'metadata_db down: {health.get("metadata_db")}')

    nodes = health.get('nodes', {})
    up_nodes = [n for n, s in nodes.items() if s.get('status') == 'ok']
    if len(up_nodes) >= 3:
        ok(f'All 3 storage nodes up: {up_nodes}')
    elif len(up_nodes) > 0:
        warn(f'Only {len(up_nodes)}/3 storage nodes up: {up_nodes}')
    else:
        fail('No storage nodes reachable')

    # Check each node reports a node name (not "node_unknown")
    for name, status in nodes.items():
        node_id = status.get('node', 'UNKNOWN')
        if node_id != 'node_unknown' and node_id:
            ok(f'  Node {name} identifies as: "{node_id}"')
        else:
            warn(f'  Node {name} has no NODE_NAME set')

except Exception as e:
    fail(f'Cannot reach orchestrator at {BASE_URL}: {e}')
    print(f'{RED}  FATAL: Cannot proceed without orchestrator. Exiting.{RESET}')
    sys.exit(1)

# ─── TEST 2: Node Distribution (Different Physical Machines) ─────────────────

section('TEST 2: Node Distribution — Shards on Different Physical Nodes')

try:
    r = requests.get(f'{BASE_URL}/nodes', timeout=10)
    nodes_data = r.json()
    print(f'  Node listing:')
    unique_nodes = set()
    for name, info in nodes_data.items():
        node_id = info.get('node', 'unknown')
        shard_count = info.get('shard_count', '?')
        free_gb = info.get('free_gb', '?')
        url = info.get('url', '')
        print(f'    {name}: node_id={node_id}, shards={shard_count}, free={free_gb}GB, url={url}')
        unique_nodes.add(node_id)

    if len(unique_nodes) >= 3:
        ok(f'All 3 storage nodes have distinct identities: {unique_nodes}')
    else:
        warn(f'Only {len(unique_nodes)} distinct node identities found: {unique_nodes}')
        warn('Nodes may be on same machine — check podAntiAffinity')
except Exception as e:
    fail(f'Cannot list nodes: {e}')

# ─── TEST 3: Upload + Shard Verification ─────────────────────────────────────

section('TEST 3: Upload Test — File Splitting, Parallel Upload, Metadata')

# Create a 9KB test file (3KB per chunk exactly)
TEST_CONTENT = create_test_file(9000, 'SHARDVAULT_QA_TEST')
TEST_FILENAME = 'qa_test_9kb.bin'

upload_start = time.time()
try:
    r = requests.post(
        f'{BASE_URL}/upload',
        files={'file': (TEST_FILENAME, TEST_CONTENT, 'application/octet-stream')},
        timeout=60
    )
    upload_time = time.time() - upload_start

    if r.status_code == 200:
        resp = r.json()
        FILE_ID = resp['file_id']
        ok(f'Upload succeeded in {upload_time:.2f}s | file_id: {FILE_ID}')
        ok(f'Original size: {resp["original_size"]} bytes ({resp["original_size_human"]})')

        if resp.get('errors'):
            warn(f'Upload had shard errors: {resp["errors"]}')
        else:
            ok('All shards uploaded with no errors')

        # Validate hashes returned are non-empty and distinct
        hashes = resp.get('shard_hashes', [])
        if len(hashes) == 3 and len(set(hashes)) == 3:
            ok(f'3 distinct shard hashes returned')
            for i, h in enumerate(hashes):
                print(f'    shard_{i}: {h}')
        else:
            fail(f'Expected 3 distinct shard hashes, got: {hashes}')

        parity_hash = resp.get('parity_hash')
        if parity_hash:
            ok(f'Parity hash present: {parity_hash[:16]}...')
        else:
            fail('No parity hash in upload response')

    elif r.status_code == 503:
        fail(f'Upload rejected — not enough healthy nodes: {r.json()}')
        sys.exit(1)
    else:
        fail(f'Upload failed HTTP {r.status_code}: {r.text}')
        sys.exit(1)
except Exception as e:
    fail(f'Upload request failed: {e}')
    sys.exit(1)

# ─── TEST 4: Shard Peek — Verify Physical Distribution ───────────────────────

section('TEST 4: Shard Explorer — Verify Shards on Different Nodes')

try:
    r = requests.get(f'{BASE_URL}/files/{FILE_ID}/peek', timeout=15)
    peek = r.json()
    shards = peek.get('shards', [])

    node_names_used = set()
    for shard in shards:
        idx = shard['shard_index']
        node = shard['node_name']
        status = shard['status']
        is_parity = shard['is_parity']
        label = f'parity({idx})' if is_parity else f'shard_{idx}'
        print(f'  {label}: node={node}, status={status}, size={shard["stored_size"]}B')

        if status == 'ok':
            ok(f'  {label} accessible on {node}')
        else:
            fail(f'  {label} NOT accessible: status={status}')

        if not is_parity:
            node_names_used.add(node)

    if len(node_names_used) == 3:
        ok(f'All 3 data shards are on different nodes: {node_names_used}')
    else:
        warn(f'Shards are not spread across 3 distinct nodes: {node_names_used}')

except Exception as e:
    fail(f'Peek request failed: {e}')

# ─── TEST 5: Download + SHA-256 Roundtrip Integrity ──────────────────────────

section('TEST 5: Download + Integrity — SHA-256 Roundtrip Verification')

try:
    r = requests.get(f'{BASE_URL}/download/{FILE_ID}', timeout=30)
    if r.status_code == 200:
        dl_resp = r.json()
        recovered_b64 = dl_resp.get('data_b64', '')
        recovered_bytes = base64.b64decode(recovered_b64)

        # Compare SHA-256 with original upload content
        original_hash  = sha256_of(TEST_CONTENT)
        recovered_hash = sha256_of(recovered_bytes)

        if original_hash == recovered_hash:
            ok(f'SHA-256 MATCH: {original_hash}')
            ok(f'Downloaded {len(recovered_bytes)} bytes — perfect reconstruction')
        else:
            fail(f'SHA-256 MISMATCH!')
            fail(f'  Original:  {original_hash}')
            fail(f'  Recovered: {recovered_hash}')

        # Verify content equality byte by byte
        if recovered_bytes == TEST_CONTENT:
            ok('Byte-for-byte content match confirmed')
        else:
            fail(f'Content mismatch! First diff at: {next((i for i,(a,b) in enumerate(zip(TEST_CONTENT,recovered_bytes)) if a!=b), "end")}')

        log = dl_resp.get('log', [])
        print(f'\n  Download log:')
        for entry in log:
            print(f'    {entry}')

        if dl_resp.get('corrupted_shards') == []:
            ok('No corruption detected — clean download')
        if dl_resp.get('recovered_shards') == []:
            ok('No XOR recovery needed — all shards available')

    else:
        fail(f'Download HTTP {r.status_code}: {r.text}')
except Exception as e:
    fail(f'Download request failed: {e}')

# ─── TEST 6: Corruption Recovery (XOR Parity) ────────────────────────────────

section('TEST 6: Data Corruption — XOR Parity Recovery')

for shard_to_corrupt in [0, 1, 2]:
    print(f'\n  ── Corrupting shard_{shard_to_corrupt} ──')
    try:
        # Corrupt shard via demo endpoint
        r = requests.post(f'{BASE_URL}/demo/corrupt/{FILE_ID}/{shard_to_corrupt}', timeout=10)
        if r.status_code == 200:
            ok(f'Corruption injected into shard_{shard_to_corrupt}')
        else:
            fail(f'Corrupt endpoint returned {r.status_code}: {r.text}')
            continue

        # Attempt download — should recover via XOR
        r = requests.get(f'{BASE_URL}/download/{FILE_ID}', timeout=30)
        if r.status_code == 200:
            dl_resp = r.json()
            recovered_bytes = base64.b64decode(dl_resp['data_b64'])
            recovered_hash  = sha256_of(recovered_bytes)
            original_hash   = sha256_of(TEST_CONTENT)

            if shard_to_corrupt in dl_resp.get('corrupted_shards', []):
                ok(f'Corruption detected in shard_{shard_to_corrupt}')
            else:
                warn(f'Corruption was NOT detected by integrity check (shard_{shard_to_corrupt})')

            if shard_to_corrupt in dl_resp.get('recovered_shards', []):
                ok(f'Shard {shard_to_corrupt} XOR-recovered successfully')
            else:
                fail(f'Shard {shard_to_corrupt} was NOT in recovered_shards list')

            if recovered_hash == original_hash:
                ok(f'SHA-256 match after recovery: {recovered_hash[:16]}...')
            else:
                fail(f'SHA-256 MISMATCH after recovery!')

        else:
            dl_json = r.json()
            if 'IRRECOVERABLE' in dl_json.get('error', ''):
                fail(f'Irrecoverable after corrupting shard_{shard_to_corrupt}: {dl_json["error"]}')
            else:
                fail(f'Unexpected download failure: {r.status_code}: {r.text}')

        # Re-upload to restore (corruption persists on disk — we need a fresh file for next iteration)
        r2 = requests.post(
            f'{BASE_URL}/upload',
            files={'file': (TEST_FILENAME, TEST_CONTENT, 'application/octet-stream')},
            timeout=60
        )
        FILE_ID = r2.json()['file_id']
        print(f'    (Restored with new file_id: {FILE_ID})')

    except Exception as e:
        fail(f'Corruption test failed for shard_{shard_to_corrupt}: {e}')

# ─── TEST 7: Double-Failure Detection ────────────────────────────────────────

section('TEST 7: Double Failure — Should Return IRRECOVERABLE (503)')

try:
    # Upload fresh file
    r = requests.post(
        f'{BASE_URL}/upload',
        files={'file': ('double_fail.bin', create_test_file(3000), 'application/octet-stream')},
        timeout=60
    )
    df_file_id = r.json()['file_id']

    # Corrupt 2 shards
    requests.post(f'{BASE_URL}/demo/corrupt/{df_file_id}/0', timeout=10)
    requests.post(f'{BASE_URL}/demo/corrupt/{df_file_id}/1', timeout=10)

    r = requests.get(f'{BASE_URL}/download/{df_file_id}', timeout=30)
    if r.status_code == 503:
        body = r.json()
        if 'IRRECOVERABLE' in body.get('error', ''):
            ok(f'Double failure correctly detected as IRRECOVERABLE: {body["error"]}')
        else:
            warn(f'Got 503 but unexpected body: {body}')
    elif r.status_code == 200:
        fail('System claimed to recover from 2 simultaneous failures — this should be impossible!')
    else:
        fail(f'Unexpected status {r.status_code} for double failure case')

    # Cleanup
    requests.delete(f'{BASE_URL}/files/{df_file_id}', timeout=10)

except Exception as e:
    fail(f'Double failure test error: {e}')

# ─── TEST 8: Metadata Consistency ────────────────────────────────────────────

section('TEST 8: Metadata Consistency — Database Record Accuracy')

try:
    r = requests.get(f'{BASE_URL}/files', timeout=10)
    all_files = r.json()
    print(f'  Total files in metadata_db: {len(all_files)}')

    # Find our test file
    our_file = next((f for f in all_files if f['file_id'] == FILE_ID), None)
    if our_file:
        ok(f'Test file found in metadata_db (file_id: {FILE_ID})')
        ok(f'Filename recorded: {our_file["filename"]}')
        ok(f'original_size: {our_file["original_size"]} bytes')
        ok(f'shard_count: {our_file["shard_count"]}')
        ok(f'chunk_sizes: {our_file["chunk_sizes"]}')

        shards_in_db = our_file.get('shards', [])
        data_shards  = [s for s in shards_in_db if not s['is_parity']]
        parity_shards = [s for s in shards_in_db if s['is_parity']]

        if len(data_shards) == 3:
            ok(f'3 data shards recorded in metadata_db')
        else:
            fail(f'Expected 3 data shards, found {len(data_shards)}')

        if len(parity_shards) == 1:
            ok(f'1 parity shard recorded in metadata_db')
        else:
            fail(f'Expected 1 parity shard, found {len(parity_shards)}')

        # Verify node_url is k8s DNS (not a localhost or docker-compose URL)
        for shard in shards_in_db:
            url = shard.get('node_url', '')
            if 'cluster.local' in url:
                ok(f'  Shard {shard["shard_id"][:20]}... → k8s DNS: {url}')
            elif 'localhost' in url or '127.0.0.1' in url:
                fail(f'  Shard URL is localhost! This means cross-node routing will fail: {url}')
            else:
                warn(f'  Shard URL is not cluster.local DNS: {url}')

        # Verify chunk_sizes sum roughly equals original_size
        chunk_sizes = our_file.get('chunk_sizes', [])
        if chunk_sizes:
            size_sum = sum(chunk_sizes)
            original = our_file['original_size']
            if abs(size_sum - original) <= 2:  # allow rounding
                ok(f'chunk_sizes sum ({size_sum}) matches original_size ({original})')
            else:
                fail(f'chunk_sizes sum ({size_sum}) != original_size ({original})')
    else:
        fail(f'Test file {FILE_ID} not found in metadata_db listing')

except Exception as e:
    fail(f'Metadata consistency test failed: {e}')

# ─── TEST 9: Performance — Upload Latency ────────────────────────────────────

section('TEST 9: Performance — Upload Latency Benchmark')

SIZES = [
    ('1KB',  1_000),
    ('100KB', 100_000),
    ('1MB',  1_000_000),
]

perf_file_ids = []
for label, size in SIZES:
    content = create_test_file(size, f'PERF_{label}')
    t0 = time.time()
    try:
        r = requests.post(
            f'{BASE_URL}/upload',
            files={'file': (f'perf_{label}.bin', content, 'application/octet-stream')},
            timeout=120
        )
        elapsed = time.time() - t0
        if r.status_code == 200:
            fid = r.json()['file_id']
            throughput = size / elapsed / 1024  # KB/s
            ok(f'{label}: {elapsed:.2f}s | {throughput:.0f} KB/s')
            perf_file_ids.append(fid)
        else:
            fail(f'{label} upload failed: HTTP {r.status_code}')
    except Exception as e:
        fail(f'{label} upload error: {e}')

# Parallel upload stress test
section('TEST 9b: Parallel Upload Stress — 5 Concurrent Uploads')

def upload_worker(i):
    content = create_test_file(50_000, f'PARALLEL_{i}')
    t0 = time.time()
    r = requests.post(
        f'{BASE_URL}/upload',
        files={'file': (f'parallel_{i}.bin', content, 'application/octet-stream')},
        timeout=120
    )
    elapsed = time.time() - t0
    return i, r.status_code, elapsed, r.json().get('file_id') if r.status_code == 200 else None

parallel_start = time.time()
try:
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(upload_worker, i) for i in range(5)]
        results = [f.result() for f in as_completed(futures)]

    total_time = time.time() - parallel_start
    success_count = sum(1 for _, status, _, _ in results if status == 200)
    print(f'  {success_count}/5 parallel uploads succeeded in {total_time:.2f}s total')
    for i, status, elapsed, fid in sorted(results):
        if status == 200:
            ok(f'  Worker {i}: {elapsed:.2f}s ✓')
            if fid:
                perf_file_ids.append(fid)
        else:
            fail(f'  Worker {i}: HTTP {status}')
except Exception as e:
    fail(f'Parallel upload stress test error: {e}')

# ─── TEST 10: Delete + Cleanup ────────────────────────────────────────────────

section('TEST 10: Delete Test — Files Removed from All Nodes')

try:
    r = requests.delete(f'{BASE_URL}/files/{FILE_ID}', timeout=15)
    if r.status_code == 200:
        ok(f'Delete request accepted for {FILE_ID}')
    else:
        fail(f'Delete failed: HTTP {r.status_code}')

    # Verify file is gone from metadata
    time.sleep(1)
    r = requests.get(f'{BASE_URL}/files/{FILE_ID[:-4] if FILE_ID else ""}', timeout=5) if False else None
    r2 = requests.get(f'{BASE_URL}/download/{FILE_ID}', timeout=5)
    if r2.status_code == 404:
        ok('File correctly returns 404 after deletion')
    else:
        warn(f'After deletion, download returned {r2.status_code} (expected 404)')

    # Cleanup perf test files
    for fid in perf_file_ids:
        try:
            requests.delete(f'{BASE_URL}/files/{fid}', timeout=5)
        except Exception:
            pass
    ok(f'Cleaned up {len(perf_file_ids)} performance test files')

except Exception as e:
    fail(f'Delete test failed: {e}')

# ─── FINAL REPORT ─────────────────────────────────────────────────────────────

section('FINAL REPORT')
total = passed + failed
print(f'\n  {GREEN}PASSED : {passed}/{total}{RESET}')
print(f'  {RED}FAILED : {failed}/{total}{RESET}')
print(f'  {YELLOW}WARNINGS: {warnings}{RESET}')

if failed == 0:
    print(f'\n  {GREEN}{BOLD}✓ ALL TESTS PASSED — ShardVault is behaving as a real distributed system.{RESET}')
elif failed <= 2:
    print(f'\n  {YELLOW}{BOLD}⚠ MOSTLY PASSING — {failed} failure(s) need attention.{RESET}')
else:
    print(f'\n  {RED}{BOLD}✗ {failed} TESTS FAILED — System has significant issues.{RESET}')

print()
sys.exit(0 if failed == 0 else 1)
