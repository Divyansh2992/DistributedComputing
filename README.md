# ShardVault

**Distributed File Storage System with Fault Tolerance & Docker Deployment**

ShardVault is a distributed file storage system built from scratch in Python and deployed on a multi-node **Docker** setup across multiple PCs. It splits files into shards, computes XOR parity for fault tolerance, and recovers data automatically when a storage node goes down — all with atomic uploads, JWT authentication, and full Prometheus observability.

> Built to demonstrate distributed systems engineering: sharding, parity-based recovery, Docker networking across LANs, and chaos testing — not just as a demo, but with honest tradeoff documentation.

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Key Features](#key-features)
- [Tech Stack](#tech-stack)
- [How It Works](#how-it-works)
- [Fault Tolerance & Recovery](#fault-tolerance--recovery)
- [Deployment Guide](#deployment-guide)
- [Testing & Validation](#testing--validation)
- [Demo Instructions](#demo-instructions)
- [Observability](#observability)
- [Project Structure](#project-structure)
- [Future Improvements](#future-improvements)
- [What This Project Demonstrates](#what-this-project-demonstrates)

---

## Architecture Overview

ShardVault runs as five isolated microservices across 3 separate PCs in a LAN, using Docker Compose on each PC.

```
                       ┌────────────────────────────────────────────────────────┐
                       │                   PC 1 (MASTER)                        │
                       │                 (192.168.x.y)                          │
                       │                                                        │
Client (curl / UI)     │  ┌──────────────────────────────────────────────────┐ │
──────────────────►    │  │         Orchestrator (port 5000)                 │ │
192.168.x.y:5000       │  │      + UI Dashboard (index.html)                 │ │
                       │  │                                                  │ │
                       │  │  • Upload: shard → XOR parity → parallel write │ │
                       │  │  • Download: fetch all shards → XOR recover    │ │
                       │  │  • SHA-256 integrity check on every shard      │ │
                       │  │  • Atomic upload with rollback on failure      │ │
                       │  │  • Prometheus /metrics endpoint                │ │
                       │  └──────────┬──────────────┬──────────────────────┘ │
                       │             │              │                        │
                       │  ┌──────────▼────┐  ┌─────▼────────────┐           │
                       │  │ Auth Service   │  │  Metadata DB     │           │
                       │  │ (port 5001)    │  │  (port 5005)     │           │
                       │  │                │  │                  │           │
                       │  │ JWT tokens     │  │ SQLite Database  │           │
                       │  │ HS256 / 24h    │  │ file→shard map   │           │
                       │  └────────────────┘  └──────────────────┘           │
                       └────────────────────────────────────────────────────────┘
                                          │
                    ┌─────────────────────┼──────────────────────┐
                    │                     │                      │
        ┌───────────▼──────────┐ ┌───────▼──────────┐  ┌────────▼─────────┐
        │    PC 2 (NODE_A)     │ │  PC 3 (NODE_B/C) │  │   Optional PC 4   │
        │  (192.168.x.z)       │ │ (192.168.x.w)    │  │   (if scaling)    │
        │                      │ │                  │  │                   │
        │  ┌────────────────┐  │ │ ┌──────────────┐ │  │ ┌──────────────┐  │
        │  │  Storage Node  │  │ │ │ Storage Node │ │  │ │ Storage Node │  │
        │  │      A         │  │ │ │      B       │ │  │ │      D       │  │
        │  │   (port 5002)  │  │ │ │ (port 5003)  │ │  │ │ (port 5006)  │  │
        │  │  /data/shards  │  │ │ │ /data/shards │ │  │ │ /data/shards │  │
        │  │   + Storage    │  │ │ │   + Storage  │ │  │ │   + Storage  │  │
        │  │    Node C      │  │ │ │     Node C   │ │  │ │    Node...   │  │
        │  │  (port 5004)   │  │ │ │ (port 5004)  │ │  │ │ (port 500x)  │  │
        │  │  /data/shards  │  │ │ │ /data/shards │ │  │ │ /data/shards │  │
        │  └────────────────┘  │ │ └──────────────┘ │  │ └──────────────┘  │
        └──────────────────────┘ └──────────────────┘  └────────────────────┘
               Docker Compose          Docker Compose         Docker Compose
            (docker-compose.yml)   (docker-compose.yml)   (docker-compose.yml)
```

### Service Breakdown

| Service | Port | Role | Location |
|---|---|---|---|
| `orchestrator` | `5000` | Core logic: sharding, download, recovery, API routing | PC 1 (Master) |
| `auth_service` | `5001` | Issues and verifies JWT tokens (HS256, 24h TTL) | PC 1 (Master) |
| `metadata_db` | `5005` | SQLite database for file-to-shard mappings | PC 1 (Master) |
| `storage-node-0 (Node A)` | `5002` | Storage node for shard_0 | PC 2 |
| `storage-node-1 (Node B)` | `5003` | Storage node for shard_1 | PC 3 |
| `storage-node-2 (Node C)` | `5004` | Storage node for shard_2 + parity | PC 3 |

---

## Key Features

### Distributed Storage Across Multiple PCs
- Files are split into 3 equal shards and distributed across 3 independent physical PCs
- Each storage node runs in its own Docker container on a separate PC
- Node communication via LAN IPs configured in a **single `.env` file** — change IPs once, entire system works

### XOR Parity & Fault Tolerance
- A 4th parity shard is computed as `shard_0 ⊕ shard_1 ⊕ shard_2` and stored on its own node
- Any **single** missing or corrupted shard can be fully recovered from parity + the two surviving shards
- SHA-256 integrity check is performed on every fetched shard before reassembly — silent corruption is detected and triggers XOR recovery automatically

### Optimal Parity Placement
- Parity is placed on `node_2` (alongside `shard_2`) after an audit revealed that co-locating parity with `shard_0` made `node_0` failure silently irrecoverable
- This is honestly documented: `node_0` and `node_1` failures are fully recoverable; `node_2` failure is the documented single point of parity loss
- For 3-node systems, this is the best achievable fault tolerance (2-of-3 nodes)

### Parallel Uploads with Retry
- All 4 shards (3 data + 1 parity) are uploaded to storage nodes concurrently using `ThreadPoolExecutor`
- Each upload is retried up to 3 times with exponential backoff (0.5s → 1s → 2s) on 5xx or network errors

### Atomic Uploads with Rollback
- If any shard write fails after others have succeeded, the orchestrator rolls back all successful writes before returning an error
- Metadata is only persisted after ALL 4 shard writes succeed — the database never contains a partial upload

### Simple Multi-PC Deployment
- Single `docker-compose.yml` on each PC using environment variables from `.env` file
- All service-to-service communication uses LAN IP addresses configured centrally
- No DNS resolution needed — direct IP + port communication across the network
- Works on Windows, Linux, or macOS with Docker installed

### Observability
- Real Prometheus `/metrics` endpoint (Prometheus exposition format) on both the orchestrator and each storage node
- Tracked metrics: uploads, downloads, XOR recovery count, upload/download latency histograms, files stored gauge, authentication failures, shard read/write counts

### Chaos Testing
- `chaos_test.sh`: Bash script — kills containers, resets networks, verifies data persistence, validates recovery scenarios
- `recovery_suite.py`: Python test suite — baseline integrity, corruption injection, node kill scenarios, double failure, retry validation, parity placement verification
- `test_xor_parity.py`: Unit tests for the XOR parity math (edge cases: empty shards, odd sizes, byte boundaries)

---

## Tech Stack

| Layer | Technology |
|---|---|
| **Language** | Python 3.11 |
| **Framework** | Flask + flask-cors |
| **Auth** | PyJWT (HS256) |
| **Metadata Store** | SQLite with WAL mode |
| **Containerisation** | Docker |
| **Orchestration** | Docker Compose (multi-PC) |
| **Storage** | Local filesystem (`/data/shards` volumes) |
| **Networking** | LAN IP configuration via `.env` file |
| **Observability** | Prometheus v2.51.0 + `prometheus_client` |
| **Testing** | Python `unittest` + bash |

---

## How It Works

### Upload Flow

```
POST /upload  (multipart/form-data)
     │
     ├─ 1. Health check — requires all 3 nodes healthy before accepting upload
     │
     ├─ 2. Split file into 3 chunks
     │       chunk_0 = bytes[0 : L/3]
     │       chunk_1 = bytes[L/3 : 2L/3]
     │       chunk_2 = bytes[2L/3 : ]
     │
     ├─ 3. Compute XOR parity (zero-padded to equal length)
     │       parity = chunk_0 ⊕ chunk_1 ⊕ chunk_2
     │
     ├─ 4. SHA-256 hash each chunk + parity
     │
     ├─ 5. Parallel upload (ThreadPoolExecutor, 4 workers)
     │       shard_0 → storage-node-0
     │       shard_1 → storage-node-1
     │       shard_2 → storage-node-2
     │       parity  → storage-node-2 (dynamic placement, not node_0)
     │
     ├─ 6. On any failure: delete all successfully written shards (rollback)
     │
     └─ 7. Persist file + shard metadata to metadata_db (only on full success)
```

### Download & Recovery Flow

```
GET /download/<file_id>
     │
     ├─ 1. Fetch file recipe from metadata_db
     │
     ├─ 2. Parallel fetch of all 3 primary shards (ThreadPoolExecutor)
     │
     ├─ 3. SHA-256 integrity check on each fetched shard
     │       Corrupted shard → treated as missing → triggers recovery
     │
     ├─ 4. Recovery decision:
     │       0 missing → assemble directly
     │       1 missing → fetch parity, XOR recover missing shard, verify hash
     │       2+ missing → 503 IRRECOVERABLE (never return corrupt data)
     │
     └─ 5. Assemble chunks in order, trim XOR zero-padding using stored chunk sizes
```

### XOR Recovery (1-shard loss)

Given parity = `s0 ⊕ s1 ⊕ s2`, to recover `s1`:

```
recovered_s1 = parity ⊕ s0 ⊕ s2
```

The recovered shard is verified against its stored SHA-256 hash before the file is returned. If the hash doesn't match, the system returns a `503 IRRECOVERABLE` rather than silently serving incorrect data.

---

## Fault Tolerance & Recovery

### What Is Recoverable

| Failure | Outcome |
|---|---|
| `storage-node-0` down | ✅ `shard_0` recovered from `parity ⊕ shard_1 ⊕ shard_2` |
| `storage-node-1` down | ✅ `shard_1` recovered from `parity ⊕ shard_0 ⊕ shard_2` |
| Single shard silently corrupted (any node) | ✅ Detected by SHA-256 check, recovered via XOR |
| Container crash + restart (while others healthy) | ✅ XOR recovery during restart window; Docker volumes survive restarts |
| Orchestrator container restart | ✅ JWT token cache cold-starts automatically on next request |
| Metadata DB container restart | ✅ SQLite database persisted on Docker volume, WAL mode survives restarts |

### What Is NOT Recoverable

| Failure | Outcome |
|---|---|
| `storage-node-2` down | ❌ Both `shard_2` and `parity` are on `node_2` — irrecoverable with 3-node layout |
| Any 2 nodes down simultaneously | ❌ XOR parity requires 2 of 3 shards to be present |
| Silent corruption on 2+ shards | ❌ SHA-256 detects it, returns `503 IRRECOVERABLE` — no corrupt data served |
| Metadata DB permanently lost | ❌ Without the shard location map, downloads cannot be routed |

### Design Honesty

With 3 storage nodes and a single XOR parity shard, there is unavoidably one node whose failure is irrecoverable (whichever node holds both a data shard and the parity). ShardVault chooses `node_2` to absorb this risk, making `node_0` and `node_1` failures fully recoverable. This tradeoff is documented explicitly in the source code. A 4th parity-only node would achieve true 1-in-3 fault tolerance.

---

## Deployment Guide

**See [DEPLOYMENT.md](DEPLOYMENT.md) for a complete Docker deployment guide.**

### Quick Summary:

1. **Edit `.env`** with your PC LAN IPs
2. **Copy `.env` to all 3 PCs** (must be identical)
3. **Run on each PC:**
   - PC 1: `docker-compose up --build`
   - PC 2: `docker-compose -f docker-compose.node_a.yml up --build`
   - PC 3: `docker-compose -f docker-compose.node_bc.yml up --build`
4. **Access UI** at `http://<MASTER_IP>:5000`

For detailed instructions, see [DEPLOYMENT.md](DEPLOYMENT.md).

---

## Testing & Validation

### Unit Tests — XOR Parity Math

```bash
python3 -m pytest tests/test_xor_parity.py -v
```

Tests verify correctness of XOR recovery across edge cases:
- 3-byte files (each chunk is 1 byte)
- Non-divisible file sizes (chunk padding and trimming)
- 1MB files (full-scale reconstruction)
- Deliberately corrupted shards (recovery should match original)

### Integration Tests — End-to-End System

```bash
python3 tests/test_shardvault.py
```

Covers upload, download, single-shard corruption recovery, and delete workflows against a running Docker deployment.

### Recovery Validation Suite

Full fault-injection test suite:

```bash
python3 tests/recovery_suite.py --host 192.168.1.100 --port 5000
```

**Test phases:**
1. **System Discovery** — health check, parity placement verification, port availability
2. **Baseline Integrity** — upload 6 files (3B to 1MB), SHA-256 round-trip verification
3. **Corruption Recovery** — inject per-shard corruption, verify detection + XOR recovery for all 3 shard indices
4. **Container Kill Recovery** — stop containers for each node, verify expected recovery/irrecoverable outcome
5. **Double Failure** — corrupt 2 shards simultaneously, verify `503 IRRECOVERABLE` is returned (never corrupt data)
6. **Atomicity** — verify rollback behavior when uploads fail
7. **Performance Benchmark** — upload + download latency for 1KB / 100KB / 1MB files
8. **Observability** — Prometheus metrics health check

### Chaos Test Script

```bash
MASTER_IP=192.168.1.100 bash tests/chaos_test.sh
```

**Chaos scenarios:**
1. Stop a storage node container, verify XOR recovery
2. Rapid consecutive container restarts (3 cycles), verify system survives
3. Restart orchestrator, verify JWT cache cold-start
4. Shard file verification on each node's filesystem
5. Container restart, confirm shards survive on Docker volumes
6. Network simulation scenarios (if using `docker-compose up` with custom bridge)
7. Log trace of a complete upload through orchestrator
8. Metrics verification via `/metrics` endpoints
9. Health check verification for all services

---

## Demo Instructions

### Quick Demo: Upload → Stop Container → Download with Recovery

```bash
export BASE=http://192.168.1.100:5000

# 1. Upload a file
curl -X POST $BASE/upload -F "file=@/path/to/any_file.txt"
# Note the file_id in the response, e.g. "abc123..."
export FILE_ID=<file_id from response>

# 2. Confirm a healthy baseline download
curl $BASE/download/$FILE_ID -o /tmp/downloaded.txt
echo "Downloaded successfully"

# 3. Stop storage node B (holds shard_1) — on PC 3
docker-compose -f docker-compose.node_bc.yml stop node_b

# 4. Immediately download again — XOR recovery should kick in
curl $BASE/download/$FILE_ID -o /tmp/recovered.txt
echo "Downloaded with recovery"

# 5. Restart the container
docker-compose -f docker-compose.node_bc.yml start node_b

# 6. Download again — normal read path, no recovery needed
curl $BASE/download/$FILE_ID -o /tmp/normal_download.txt
echo "Downloaded normally"

# Verify all downloads are identical
md5sum /tmp/*.txt
```

Expected output:
```
Downloaded successfully
Downloaded with recovery  (shows XOR recovery happened)
Downloaded normally
[all md5sums should match — proves no data loss]
```
export FILE_ID=<file_id from response>

# 2. Confirm a healthy baseline download
curl $BASE/download/$FILE_ID | python3 -c "
import sys, json, base64
d = json.load(sys.stdin)
print('Size:', d['size'], '| Recovered shards:', d['recovered_shards'])
"

# 3. Kill storage-node-1 (holds shard_1)
kubectl delete pod storage-node-1 -n shardvault --grace-period=0 --force

# 4. Immediately download again — XOR recovery should kick in
curl $BASE/download/$FILE_ID | python3 -c "
import sys, json, base64
d = json.load(sys.stdin)
print('Recovered shards:', d['recovered_shards'])  # should show [1]
print('Log:')
for entry in d['log']:
    print(' ', entry)
"
# Expected output includes:
#   [WARN] Shard 1 unavailable from storage-node-1: ...
#   [RECOVERY] Shard 1 missing — attempting XOR recovery
#   [RECOVERY] Shard 1 XOR-recovered successfully ✓

# 5. Wait for pod to restart automatically (StatefulSet controller)
kubectl wait pod/storage-node-1 -n shardvault --for=condition=Ready --timeout=120s

# 6. Download again — normal read path, no recovery needed
curl $BASE/download/$FILE_ID | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('Size:', d['size'], '| All shards OK')
"
```

### Corruption Demo (No Node Kill Required)

```bash
# Upload a file
curl -X POST $BASE/upload -F "file=@/path/to/file.txt"
export FILE_ID=<file_id>

# Corrupt shard 0 in-place (test API)
curl -X POST $BASE/demo/corrupt/$FILE_ID/0

# Download — orchestrator detects SHA-256 mismatch, recovers from parity
curl $BASE/download/$FILE_ID | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('Corrupted:', d['corrupted_shards'])  # [0]
print('Recovered:', d['recovered_shards'])  # [0]
"
```

### Inspect Shard Distribution

```bash
# See exactly which node holds which shard for a file
curl $BASE/files/$FILE_ID/peek | python3 -m json.tool
```

---

## Observability

### Prometheus Metrics

Access the Prometheus UI at `http://<MASTER_IP>:30090`

**Orchestrator metrics:**

| Metric | Type | Description |
|---|---|---|
| `shardvault_uploads_total` | Counter | Total upload attempts |
| `shardvault_uploads_success` | Counter | Successful uploads |
| `shardvault_uploads_failed` | Counter | Failed/rolled-back uploads |
| `shardvault_downloads_total` | Counter | Total download requests |
| `shardvault_downloads_xor_recovered` | Counter | Downloads that required XOR recovery |
| `shardvault_downloads_failed` | Counter | Downloads that could not be served |
| `shardvault_upload_duration_seconds` | Histogram | End-to-end upload latency |
| `shardvault_download_duration_seconds` | Histogram | End-to-end download latency |
| `shardvault_files_stored_total` | Gauge | Current file count in metadata DB |

**Storage node metrics (per-node):**

| Metric | Description |
|---|---|
| `shardvault_shard_writes_total` | Shard write operations |
| `shardvault_shard_reads_total` | Shard read operations |
| `shardvault_shard_deletes_total` | Shard delete operations |
| `shardvault_shard_count` | Current number of shards stored |
| `shardvault_auth_failures_total` | JWT validation failures |

### Logs

```bash
# Orchestrator — shows upload/download decisions including recovery
docker logs -f orchestrator

# Storage node — shows per-shard operations
docker logs -f node_a

# Example orchestrator log for a recovery download:
# [DOWNLOAD] file_id=abc123 | filename=report.pdf
# [WARN] Shard 1 unavailable from storage-node-1: Connection refused
# [RECOVERY] Shard 1 missing — attempting XOR recovery
# [RECOVERY] Shard 1 XOR-recovered successfully ✓
# [DONE] Download complete in 0.84s
```

### Continuous Data Integrity

Implement continuous background integrity checks by:
1. **Periodic health checks**: `curl http://<NODE_IP>:500X/health` on a schedule
2. **Shard verification**: Custom script to periodically query all stored files and verify shard reachability
3. **Automated alerts**: Monitor logs and metrics for failures detected before user-triggered downloads

---

## Project Structure

```
DistributedComputing/
├── orchestrator/
│   ├── app.py                  # Core logic: upload, download, XOR recovery, metrics
│   ├── Dockerfile
│   ├── requirements.txt
│   └── static/                 # Web UI served by Flask
│
├── auth_service/
│   ├── app.py                  # JWT token issuance (HS256, 24h TTL)
│   ├── Dockerfile
│   └── requirements.txt
│
├── metadata_db/
│   ├── app.py                  # SQLite REST API: file + shard records
│   ├── Dockerfile
│   └── requirements.txt
│
├── storage_node/
│   ├── app.py                  # Shard store/retrieve/delete + Prometheus metrics
│   ├── Dockerfile
│   └── requirements.txt
│
├── tests/
│   ├── recovery_suite.py       # Multi-phase fault injection + recovery validation
│   ├── chaos_test.sh           # Container chaos script
│   ├── test_shardvault.py      # Integration tests (upload/download/delete)
│   └── test_xor_parity.py      # XOR parity unit tests (edge cases)
│
├── docker-compose.yml          # PC 1 (Master): Orchestrator, Auth, MetaDB
├── docker-compose.node_a.yml   # PC 2: Storage Node A
├── docker-compose.node_bc.yml  # PC 3: Storage Nodes B & C
├── .env                        # LAN IP configuration (EDIT THIS)
├── DEPLOYMENT.md               # Complete Docker deployment guide
├── .gitattributes              # LF line endings for containers
└── README.md
```

---

## Future Improvements

| Improvement | Motivation |
|---|---|
| **4th parity-only node** | Eliminates the current single irrecoverable failure point; achieves true 1-in-3 fault tolerance without any co-location compromise |
| **RAID-5 style rotating parity** | Distributes parity write load evenly across all nodes rather than concentrating it on `node_2` |
| **PostgreSQL instead of SQLite** | WAL-mode SQLite works well for single-writer workloads but becomes a bottleneck under concurrent multi-file uploads; PostgreSQL adds row-level locking and horizontal replica support |
| **Auto-healing resharding** | When a container restarts after a failure, automatically re-upload the missing shard to restore full redundancy without manual intervention |
| **Dynamic node discovery** | Currently nodes are registered statically in the orchestrator; a service registry would support adding/removing nodes at runtime |
| **Erasure coding (Reed-Solomon)** | Replace XOR (1-of-3 recovery) with RS coding for configurable `k-of-n` fault tolerance without proportional storage overhead |
| **Encryption at rest** | Encrypt shard data before writing to storage nodes; current design relies on JWT auth for access control only |
| **Kubernetes deployment** | Move from Docker Compose to Kubernetes for automated scaling, pod anti-affinity enforcement, and orchestrated rolling updates |

---

## What This Project Demonstrates

**For distributed systems engineers and recruiters reviewing this project:**

| Concept | Implementation |
|---|---|
| **Data sharding** | Files split into equal-size chunks, each routed to a distinct physical node |
| **XOR parity fault tolerance** | Single-node failure recovery without replication overhead |
| **Integrity verification** | SHA-256 checked on every shard fetch; silent corruption triggers recovery, not silent data serve |
| **Atomic write semantics** | Rollback of successfully written shards if any write fails; metadata uncommitted until all-or-nothing |
| **Parallel I/O** | Concurrent shard upload and download with `ThreadPoolExecutor` |
| **Retry with backoff** | Exponential backoff (0.5s → 1s → 2s, 3 retries) on transient 5xx and network errors |
| **Multi-PC distributed deployment** | Services across multiple PCs on the same LAN, coordinated via centralized `.env` configuration |
| **Service-to-service communication** | Direct LAN IP routing; no DNS needed, configuration-driven discovery |
| **Persistent storage** | Shards survive container restarts via Docker volumes; metadata DB on dedicated volume |
| **JWT authentication** | Service-to-service auth with 24h cached tokens; cold-start recovery after orchestrator restart |
| **Prometheus observability** | Real exposition-format `/metrics` on orchestrator and all storage nodes; latency histograms |
| **Honest failure characterisation** | Documented exactly which failure scenarios are and are not recoverable, with rationale |
| **Chaos testing** | Container kills, network simulations, filesystem inspection, recovery validation |
| **Docker containerisation** | Multi-PC deployment with Docker Compose; simple `.env`-driven configuration |

---

*Deployed on multiple PCs across a LAN using Docker Compose. All components containerised. All failure scenarios tested and documented.*
