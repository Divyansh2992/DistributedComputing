# ShardVault

**Distributed File Storage System with Fault Tolerance & Kubernetes Deployment**

ShardVault is a production-deployed distributed file storage system built from scratch in Python and deployed on a multi-node **k3s** Kubernetes cluster. It splits files into shards, computes XOR parity for fault tolerance, and recovers data automatically when a storage node goes down — all with atomic uploads, JWT authentication, and full Prometheus observability.

> Built to demonstrate distributed systems engineering: sharding, parity-based recovery, Kubernetes networking, and chaos testing — not just as a demo, but with honest tradeoff documentation.

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

ShardVault runs as five isolated microservices across a 3-node Kubernetes cluster.

```
                         ┌─────────────────────────────────────────────────────────┐
                         │                   k3s Kubernetes Cluster                │
                         │                                                         │
  Client (curl / UI)     │  ┌──────────────────────────────────────────────────┐  │
  ──────────────────►    │  │               Orchestrator (port 5000)           │  │
  NodePort :30000        │  │                                                  │  │
                         │  │  • Upload: shard → XOR parity → parallel write  │  │
                         │  │  • Download: fetch all shards → XOR recover      │  │
                         │  │  • SHA-256 integrity check on every shard        │  │
                         │  │  • Atomic upload with rollback on failure        │  │
                         │  │  • Retry logic with exponential backoff          │  │
                         │  │  • Prometheus /metrics endpoint                  │  │
                         │  └───────┬────────────┬────────────────────────────┘  │
                         │          │             │                                │
                         │  ┌───────▼──────┐ ┌───▼──────────────┐               │
                         │  │  Auth Service │ │   Metadata DB    │               │
                         │  │  (port 5001)  │ │   (port 5005)    │               │
                         │  │               │ │                  │               │
                         │  │  JWT tokens   │ │  SQLite + PVC    │               │
                         │  │  HS256 / 24h  │ │  WAL mode        │               │
                         │  └───────────────┘ │  file→shard map  │               │
                         │                    └──────────────────┘               │
                         │                                                         │
                         │  ┌─────────────────────────────────────────────────┐  │
                         │  │          Storage Nodes — StatefulSet (3 pods)   │  │
                         │  │                                                  │  │
                         │  │  storage-node-0    storage-node-1   storage-node-2  │
                         │  │  Laptop A          Laptop B         Laptop C    │  │
                         │  │  /data/shards      /data/shards     /data/shards│  │
                         │  │  10Gi PVC          10Gi PVC         10Gi PVC    │  │
                         │  │                                                  │  │
                         │  │  shard_0 ──────►  node_0                        │  │
                         │  │  shard_1 ──────────────────► node_1             │  │
                         │  │  shard_2 + parity ──────────────────► node_2   │  │
                         │  └─────────────────────────────────────────────────┘  │
                         │                                                         │
                         │  ┌──────────────────────────────────────────────────┐  │
                         │  │  Prometheus (port 30090) — scrapes /metrics      │  │
                         │  │  orchestrator + all 3 storage nodes              │  │
                         │  └──────────────────────────────────────────────────┘  │
                         └─────────────────────────────────────────────────────────┘
```

### Service Breakdown

| Service | Port | Role |
|---|---|---|
| `orchestrator` | `5000` | Core logic: sharding, download, recovery, API routing |
| `auth_service` | `5001` | Issues and verifies JWT tokens (HS256, 24h TTL) |
| `metadata_db` | `5005` | SQLite database for file-to-shard mappings, persisted via PVC |
| `storage-node-{0,1,2}` | `5002` | Stateless shard servers, one pod per physical machine |
| `prometheus` | `9090` | Metrics collection and UI |

---

## Key Features

### Distributed Storage
- Files are split into 3 equal shards and distributed across 3 independent physical nodes
- Each storage node runs in its own Kubernetes pod scheduled to a different laptop via `podAntiAffinity`
- Shard routing uses stable Kubernetes headless DNS — no hard-coded IPs anywhere

### XOR Parity & Fault Tolerance
- A 4th parity shard is computed as `shard_0 ⊕ shard_1 ⊕ shard_2` and stored on its own node
- Any **single** missing or corrupted shard can be fully recovered from parity + the two surviving shards
- SHA-256 integrity check is performed on every fetched shard before reassembly — silent corruption is detected and triggers XOR recovery automatically

### Dynamic Parity Placement
- Parity is placed on `node_2` (not `node_0`) after an audit revealed that co-locating parity with `shard_0` made `node_0` failure silently irrecoverable
- This is honestly documented: `node_0` and `node_1` failures are fully recoverable; `node_2` failure is the documented single point of parity loss

### Parallel Uploads with Retry
- All 4 shards (3 data + 1 parity) are uploaded to storage nodes concurrently using `ThreadPoolExecutor`
- Each upload is retried up to 3 times with exponential backoff (0.5s → 1s → 2s) on 5xx or network errors

### Atomic Uploads with Rollback
- If any shard write fails after others have succeeded, the orchestrator rolls back all successful writes before returning an error
- Metadata is only persisted after ALL 4 shard writes succeed — the database never contains a partial upload

### Kubernetes-Native Deployment
- 3 storage nodes run as a `StatefulSet` with stable pod names and headless DNS (`storage-node-0.storage-node.shardvault.svc.cluster.local`)
- Each pod has a dedicated 10Gi `PersistentVolumeClaim` backed by k3s `local-path` storage — shards survive pod restarts
- `podAntiAffinity` with `requiredDuringScheduling` enforces true physical separation across laptops

### Observability
- Real Prometheus `/metrics` endpoint (Prometheus exposition format) on both the orchestrator and each storage node
- Tracked metrics: uploads, downloads, XOR recovery count, upload/download latency histograms, files stored gauge, authentication failures, shard read/write counts

### Chaos Testing
- `chaos_test.sh`: 10-section bash script — kills pods, introduces network delay via `tc qdisc`, verifies PVC persistence, checks DNS resolution from inside pods, validates Prometheus scraping
- `recovery_suite.py`: 8-phase Python test suite — baseline integrity, corruption injection, node kill scenarios, double failure, retry validation, parity co-location verification, performance benchmarking, observability checks
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
| **Orchestration** | Kubernetes (k3s v1.29) |
| **Storage** | Kubernetes PVC (`local-path` StorageClass) |
| **Networking** | CoreDNS + Flannel (k3s default) |
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
| Pod crash + restart (while others healthy) | ✅ XOR recovery during crash window; PVC data survives restart |
| Orchestrator pod restart | ✅ JWT token cache cold-starts automatically on next request |
| Metadata DB pod restart | ✅ SQLite database persisted on PVC, WAL mode survives restarts |

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

### Prerequisites

- 3 laptops connected on the same LAN
- k3s installed on all 3 (one as master, two as workers)
- A private Docker registry accessible from all nodes (e.g., `192.168.1.10:5000`)
- `kubectl` configured on the master

### Step 1: Build and Push Images

Run on the machine that will build the images (typically the master):

```bash
# Set your registry address
export REGISTRY_HOST=192.168.1.10

# Auth service
docker build -t $REGISTRY_HOST:5000/shardvault/auth-service:latest ./auth_service
docker push $REGISTRY_HOST:5000/shardvault/auth-service:latest

# Metadata DB
docker build -t $REGISTRY_HOST:5000/shardvault/metadata-db:latest ./metadata_db
docker push $REGISTRY_HOST:5000/shardvault/metadata-db:latest

# Storage node
docker build -t $REGISTRY_HOST:5000/shardvault/storage-node:latest ./storage_node
docker push $REGISTRY_HOST:5000/shardvault/storage-node:latest

# Orchestrator
docker build -t $REGISTRY_HOST:5000/shardvault/orchestrator:latest ./orchestrator
docker push $REGISTRY_HOST:5000/shardvault/orchestrator:latest
```

### Step 2: Update Image References

Replace `REGISTRY_HOST` in the k8s manifests with your actual registry IP:

```bash
sed -i 's/REGISTRY_HOST/192.168.1.10/g' k8s/*.yaml
```

### Step 3: Deploy to Kubernetes

```bash
# Apply all manifests in order
kubectl apply -f k8s/00-namespace.yaml
kubectl apply -f k8s/01-secret.yaml
kubectl apply -f k8s/02-auth-service.yaml
kubectl apply -f k8s/03-metadata-db.yaml
kubectl apply -f k8s/04-storage-nodes.yaml
kubectl apply -f k8s/05-orchestrator.yaml
kubectl apply -f k8s/06-prometheus.yaml
kubectl apply -f k8s/07-cronjob-integrity.yaml

# Or using kustomize
kubectl apply -k k8s/
```

### Step 4: Verify Deployment

```bash
# Watch pods come up
kubectl get pods -n shardvault -w

# Verify physical node distribution (critical — each storage pod must be on a different laptop)
kubectl get pods -n shardvault -o wide | grep storage-node

# Check PVCs are bound
kubectl get pvc -n shardvault

# Test the API
curl http://<MASTER_IP>:30000/health
```

Expected pod distribution (one per laptop):

```
NAME               READY   STATUS    NODE
storage-node-0     1/1     Running   laptop-a
storage-node-1     1/1     Running   laptop-b
storage-node-2     1/1     Running   laptop-c
```

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
python3 -m pytest tests/test_shardvault.py -v
```

Covers upload, download, single-shard corruption recovery, and delete workflows against a live cluster.

### Recovery Validation Suite

Full fault-injection test suite. Run from the k3s master:

```bash
python3 tests/recovery_suite.py --host 192.168.1.10 --port 30000 --namespace shardvault
```

**8 test phases:**
1. **System Discovery** — health check, parity placement audit, kubectl availability
2. **Baseline Integrity** — upload 6 files (3B to 1MB), SHA-256 round-trip verification
3. **Corruption Recovery** — inject per-shard corruption, verify detection + XOR recovery for all 3 shard indices
4. **Node Kill Recovery** — `kubectl delete pod` for each of 3 nodes, verify expected recovery/irrecoverable outcome
5. **Double Failure** — corrupt 2 shards simultaneously, verify `503 IRRECOVERABLE` is returned (never corrupt data)
6. **Atomicity** — static analysis verifies rollback code paths; upload count tracked before/after
7. **Performance Benchmark** — upload + download latency for 1KB / 100KB / 1MB files
8. **Observability** — Prometheus health check and metric queries

### Chaos Test Script

```bash
MASTER_IP=192.168.1.10 bash tests/chaos_test.sh
```

**10 chaos scenarios:**
1. Kill `storage-node-1`, verify XOR recovery mid-kill
2. Rapid consecutive pod restarts (3 cycles), verify system survives
3. Orchestrator pod restart, verify JWT cache cold-start
4. Physical node distribution check (`podAntiAffinity` enforcement)
5. Shard file verification via `kubectl exec` on each node's filesystem
6. PVC persistence: restart a pod, confirm shards survive on disk
7. Network delay simulation via `tc qdisc netem 2000ms`
8. Log trace of a complete upload through orchestrator
9. Prometheus metrics verification (`/api/v1/query`)
10. CoreDNS resolution check for all 5 service DNS names from inside the orchestrator pod

---

## Demo Instructions

### Quick Demo: Upload → Kill Node → Download with Recovery

```bash
export BASE=http://192.168.1.10:30000

# 1. Upload a file
curl -X POST $BASE/upload -F "file=@/path/to/any_file.txt"
# Note the file_id in the response, e.g. "abc123..."
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
kubectl logs -n shardvault -l app=orchestrator --follow

# Storage node — shows per-shard operations
kubectl logs -n shardvault storage-node-0 --follow

# Example orchestrator log for a recovery download:
# [DOWNLOAD] file_id=abc123 | filename=report.pdf
# [WARN] Shard 1 unavailable from storage-node-1: Connection refused
# [RECOVERY] Shard 1 missing — attempting XOR recovery
# [RECOVERY] Shard 1 XOR-recovered successfully ✓
# [DONE] Download complete in 0.84s
```

### Scheduled Integrity Check

A Kubernetes `CronJob` (`07-cronjob-integrity.yaml`) runs a background integrity scan on a schedule. It checks all stored files by requesting their shard status and flagging any that are missing or unreachable — useful for detecting silent node failures before a user-triggered download surfaces them.

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
├── k8s/
│   ├── 00-namespace.yaml       # shardvault namespace
│   ├── 01-secret.yaml          # JWT_SECRET
│   ├── 02-auth-service.yaml    # Auth service Deployment + Service
│   ├── 03-metadata-db.yaml     # Metadata DB StatefulSet + PVC
│   ├── 04-storage-nodes.yaml   # Storage StatefulSet + Headless Service + PVCs
│   ├── 05-orchestrator.yaml    # Orchestrator Deployment + NodePort
│   ├── 06-prometheus.yaml      # Prometheus Deployment + ConfigMap + NodePort
│   ├── 07-cronjob-integrity.yaml  # Scheduled integrity check
│   └── kustomization.yaml
│
├── tests/
│   ├── recovery_suite.py       # 8-phase fault injection + recovery validation
│   ├── chaos_test.sh           # 10-scenario bash chaos script
│   ├── test_shardvault.py      # Integration tests (upload/download/delete)
│   └── test_xor_parity.py      # XOR parity unit tests (edge cases)
│
├── docker-compose.yml          # Local single-machine development setup
├── .gitattributes              # LF line endings for Linux-built containers
└── README.md
```

---

## Future Improvements

| Improvement | Motivation |
|---|---|
| **4th parity-only node** | Eliminates the current single irrecoverable failure point; achieves true 1-in-3 fault tolerance without any co-location compromise |
| **RAID-5 style rotating parity** | Distributes parity write load evenly across all nodes rather than concentrating it on `node_2` |
| **PostgreSQL instead of SQLite** | WAL-mode SQLite works well for single-writer workloads but becomes a bottleneck under concurrent multi-file uploads; PostgreSQL adds row-level locking and horizontal replica support |
| **Auto-healing resharding** | When a pod restarts after a failure, automatically re-upload the missing shard to restore full redundancy without manual intervention |
| **Dynamic node discovery** | Currently nodes are registered statically in the orchestrator; a service registry would support adding/removing nodes at runtime |
| **Erasure coding (Reed-Solomon)** | Replace XOR (1-of-3 recovery) with RS coding for configurable `k-of-n` fault tolerance without proportional storage overhead |
| **Encryption at rest** | Encrypt shard data before writing to storage nodes; current design relies on JWT auth for access control only |

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
| **Kubernetes StatefulSet deployment** | Stable pod DNS, per-pod PVCs, parallel pod startup, anti-affinity enforcement |
| **Kubernetes headless services** | Direct stable DNS routing to individual pods without a load balancer in the data path |
| **Persistent storage** | Shards survive pod restarts via `local-path` PVCs; metadata DB on dedicated PVC |
| **JWT authentication** | Service-to-service auth with 24h cached tokens; cold-start recovery after orchestrator restart |
| **Prometheus observability** | Real exposition-format `/metrics` on orchestrator and all storage nodes; latency histograms |
| **Honest failure characterisation** | Documented exactly which failure scenarios are and are not recoverable, with rationale |
| **Chaos testing** | Pod kills, network delays, filesystem inspection via `kubectl exec`, DNS resolution probes |

---

*Deployed on a 3-laptop k3s cluster. All components containerised. All failure scenarios tested and documented.*
