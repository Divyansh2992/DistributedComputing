#!/usr/bin/env python3
"""
ShardVault LAN — End-to-End Demo + Test Script
===============================================
Run from Laptop 1 (after all services are started):

  python demo.py --host 192.168.1.100 --port 5000

Or just against localhost:
  python demo.py

What this does:
  1. Health check — verifies all 5 services are reachable
  2. Upload files of 3 sizes (1KB, 100KB, 1MB)
  3. Download + verify SHA-256 (round-trip integrity)
  4. Corrupt Shard 0 — verify detection + XOR recovery
  5. Show instructions for manual node-kill demo

Requires: pip install requests
"""

import sys
import os
import argparse
import base64
import hashlib
import time
import json
import requests

# ─── CLI ─────────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser()
parser.add_argument('--host', default='localhost')
parser.add_argument('--port', default=5000, type=int)
args   = parser.parse_args()

BASE = f'http://{args.host}:{args.port}'

# ─── Colors ──────────────────────────────────────────────────────────────────
G = '\033[92m'; R = '\033[91m'; Y = '\033[93m'; C = '\033[96m'; B = '\033[1m'; E = '\033[0m'

passed = failed = 0

def ok(msg):
    global passed; passed += 1
    print(f'  {G}✓ PASS{E} {msg}')

def fail(msg, detail=''):
    global failed; failed += 1
    d = f' | {detail}' if detail else ''
    print(f'  {R}✗ FAIL{E} {msg}{d}')

def section(title):
    print(f'\n{B}{C}{"─"*60}{E}\n{B}{C}  {title}{E}\n{B}{C}{"─"*60}{E}')

def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def make_content(size: int, seed: str = 'SV') -> bytes:
    pattern = (seed * ((size // len(seed)) + 2)).encode()
    return bytes([b ^ (i % 256) for i, b in enumerate(pattern[:size])])


# ─── Phase 1: Health Check ────────────────────────────────────────────────────
section('PHASE 1: SYSTEM HEALTH CHECK')

try:
    r = requests.get(f'{BASE}/health', timeout=8)
    h = r.json()
    print(f'\n  Orchestrator  : {G}OK{E}')
    auth_s = h.get('auth_service', {}).get('status')
    meta_s = h.get('metadata_db',  {}).get('status')
    print(f'  Auth Service  : {G if auth_s=="ok" else R}{auth_s}{E}')
    print(f'  Metadata DB   : {G if meta_s=="ok" else R}{meta_s}{E}')
    print()
    nodes_up = 0
    for name, nd in h.get('nodes', {}).items():
        st = nd.get('status', 'down')
        print(f'  {name}: {G if st=="ok" else R}{st}{E} | shards={nd.get("shard_count","?")} | free={nd.get("free_gb","?")}GB')
        if st == 'ok':
            nodes_up += 1

    if nodes_up == 3 and auth_s == 'ok' and meta_s == 'ok':
        ok(f'All 5 services healthy — {nodes_up}/3 nodes online')
    elif nodes_up > 0:
        fail(f'Only {nodes_up}/3 nodes are up — check node startups')
        print(f'\n  {Y}TIP: Make sure storage nodes are running on Laptop 2 and Laptop 3.')
        print(f'       Check config.py has the correct LAN IPs for NODE_A_URL, NODE_B_URL, NODE_C_URL.{E}')
        sys.exit(1)
    else:
        fail('No storage nodes reachable — cannot continue')
        sys.exit(1)
except Exception as e:
    fail(f'Cannot reach orchestrator at {BASE}: {e}')
    print(f'\n  {Y}Make sure Laptop 1 is running: python orchestrator.py{E}')
    sys.exit(1)


# ─── Phase 2: Upload + Download round-trip ────────────────────────────────────
section('PHASE 2: UPLOAD + DOWNLOAD (SHA-256 INTEGRITY)')

TEST_FILES = {
    '1KB':   make_content(1_000,   'AB'),
    '100KB': make_content(100_000, 'CD'),
    '1MB':   make_content(1_000_000, 'EF'),
}
file_ids = {}

for label, content in TEST_FILES.items():
    t0 = time.time()
    try:
        r = requests.post(f'{BASE}/upload', files={'file': (f'test_{label}.bin', content, 'application/octet-stream')}, timeout=60)
        d = r.json()
        if r.status_code == 200:
            fid = d['file_id']
            file_ids[label] = (fid, content)
            ok(f'Upload {label} in {time.time()-t0:.2f}s | file_id={fid[:8]}...')
        else:
            fail(f'Upload {label}: HTTP {r.status_code}', d.get('error',''))
    except Exception as e:
        fail(f'Upload {label} exception', str(e))

print()
for label, (fid, content) in file_ids.items():
    try:
        r  = requests.get(f'{BASE}/download/{fid}', timeout=60)
        d  = r.json()
        if r.status_code == 200:
            got  = base64.b64decode(d['data_b64'])
            if sha256(got) == sha256(content):
                ok(f'Download {label}: SHA-256 MATCH — byte-perfect reconstruction')
            else:
                fail(f'Download {label}: SHA-256 MISMATCH!')
        else:
            fail(f'Download {label}: HTTP {r.status_code}', d.get('error',''))
    except Exception as e:
        fail(f'Download {label} exception', str(e))


# ─── Phase 3: Corruption + XOR Recovery ──────────────────────────────────────
section('PHASE 3: CORRUPTION DETECTION + XOR RECOVERY')

if file_ids:
    label, (fid, content) = list(file_ids.items())[1]   # use 100KB file
    print(f'\n  Using file: {label} (file_id={fid[:8]}...)')

    # Fresh upload for corruption test
    r   = requests.post(f'{BASE}/upload', files={'file': ('corrupt_test.bin', content, 'application/octet-stream')}, timeout=60)
    cfid = r.json()['file_id']
    ok(f'Fresh upload for corruption test: {cfid[:8]}...')

    # Corrupt shard 0
    cr = requests.post(f'{BASE}/demo/corrupt/{cfid}/0', timeout=10)
    if cr.status_code == 200:
        ok(f'Corruption injected into shard 0 on {cr.json().get("node","?")}')
    else:
        fail('Corruption inject failed')

    # Download — should trigger XOR recovery
    r = requests.get(f'{BASE}/download/{cfid}', timeout=30)
    d = r.json()
    if r.status_code == 200:
        got = base64.b64decode(d['data_b64'])
        if sha256(got) == sha256(content):
            ok(f'Post-corruption download: SHA-256 MATCH | XOR-recovered shards: {d.get("recovered_shards",[])}')
        else:
            fail('Post-corruption download: SHA-256 MISMATCH — recovery produced wrong data')
    else:
        fail(f'Post-corruption download failed: HTTP {r.status_code}', d.get('error',''))

    # Cleanup
    requests.delete(f'{BASE}/files/{cfid}', timeout=5)
else:
    print(f'  {Y}Skipped — no uploads succeeded{E}')


# ─── Phase 4: Cleanup ─────────────────────────────────────────────────────────
section('PHASE 4: CLEANUP')

for label, (fid, _) in file_ids.items():
    try:
        r = requests.delete(f'{BASE}/files/{fid}', timeout=5)
        ok(f'Deleted {label} ({fid[:8]}...)')
    except Exception as e:
        fail(f'Delete {label}', str(e))


# ─── Final Report ─────────────────────────────────────────────────────────────
section('FINAL REPORT')
print(f'\n  {G if failed==0 else R}{B}PASSED: {passed} | FAILED: {failed}{E}\n')


# ─── Manual Demo Instructions ─────────────────────────────────────────────────
section('MANUAL NODE-KILL DEMO (run separately)')
print(f"""
  STEP 1: Upload a file via the UI at http://{args.host}:{args.port}

  STEP 2: Kill storage-node-1 (Node B) on Laptop 3:
    {Y}Windows:{E}  Ctrl+C in the "ShardVault Node-B" terminal window
    {Y}Linux:{E}    kill $(lsof -ti:5003)

  STEP 3: Try to download the file from the UI.
           → Orchestrator detects node down
           → Fetches shard 0 (Node A) + shard 2 + parity (Node C)
           → XOR-reconstructs shard 1
           → File downloads successfully with RECOVERED badge

  STEP 4: Restart Node B:
    {Y}Windows:{E}  Re-run start_node_laptop3.bat
    {Y}Linux:{E}    NODE_NAME=storage-node-1 PORT=5003 python3 storage_node.py

  STEP 5: Corrupt a shard:
    → Click "⚠ Corrupt S0" on any file row in the UI
    → Click Download → see "PARITY: Shard 0 recovered from parity" in the log
""")
