# Distributed Sharded Storage with Fault Tolerance

A full Docker-based distributed storage system using 6 containers, demonstrating file sharding, redundancy, integrity checking, and a modern dashboard UI.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        User Browser                             │
│                    (React/HTML Dashboard)                       │
└────────────────────────┬────────────────────────────────────────┘
                         │ HTTP
┌────────────────────────▼────────────────────────────────────────┐
│              File Orchestrator (Port 5000)                      │
│   - Splits files into 3 shards + 1 parity shard                │
│   - Distributes to storage nodes via JWT-authenticated calls    │
│   - Reconstructs files from available shards (fault tolerant)  │
│   - Verifies SHA-256 checksums before reconstruction           │
└───────┬────────────┬──────────────┬──────────────┬─────────────┘
        │            │              │              │
   ┌────▼───┐   ┌───▼────┐   ┌────▼───┐   ┌─────▼──────────────┐
   │Auth Svc│   │Node A  │   │Node B  │   │Node C + Metadata DB│
   │Port    │   │Port    │   │Port    │   │Port 5004 + SQLite  │
   │5001    │   │5002    │   │5003    │   │                    │
   └────────┘   └────────┘   └────────┘   └────────────────────┘
```

---

## Container Layout (6 services in docker-compose)

| Container | Role | Port | Technology |
|---|---|---|---|
| `orchestrator` | Master controller | 5000 | Python/Flask |
| `auth_service` | JWT gatekeeper | 5001 | Python/Flask |
| `node_a` | Shard storage | 5002 | Python/Flask |
| `node_b` | Shard storage | 5003 | Python/Flask |
| `node_c` | Shard storage (parity) | 5004 | Python/Flask |
| `metadata_db` | SQLite via REST | 5005 | Python/Flask |

> **Note:** All services are Python/Flask microservices in Docker. A single `docker-compose.yml` orchestrates them. The frontend is a standalone `index.html` served by the orchestrator at port 5000.

---

## Proposed File Structure

```
Project/
├── docker-compose.yml
├── README.md
│
├── auth_service/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app.py              # JWT issuing + verification
│
├── orchestrator/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── app.py              # Core logic: shard/reconstruct/fault-tolerant
│   └── static/
│       └── index.html      # Beautiful dashboard UI
│
├── storage_node/           # ONE shared codebase, 3 deployments
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app.py              # Simple: store & retrieve shards
│
└── metadata_db/
    ├── Dockerfile
    ├── requirements.txt
    └── app.py              # SQLite REST API for file recipes
```

---

## Key Feature Implementation

### 1. File Sharding (Orchestrator)
- Read uploaded file bytes → encode as Base64
- Split into 3 equal parts: `[0:L/3]`, `[L/3:2L/3]`, `[2L/3:]`
- Compute **SHA-256 hash** for each shard
- Create **4th parity shard** = duplicate of Part 2 (stored on Node C alongside Part 3)
- POST each shard (with JWT in Authorization header) to dedicated nodes

### 2. Metadata DB Schema (SQLite)
```json
{
  "file_id": "uuid-xxx",
  "filename": "cat.jpg",
  "total_size": 3145728,
  "shards": [
    {"shard_id": "A-101", "node": "node_a", "index": 0, "hash": "sha256..."},
    {"shard_id": "B-202", "node": "node_b", "index": 1, "hash": "sha256..."},
    {"shard_id": "C-303", "node": "node_c", "index": 2, "hash": "sha256..."},
    {"shard_id": "C-404", "node": "node_c", "index": 1, "hash": "sha256...", "is_parity": true}
  ]
}
```

### 3. Fault-Tolerant Reconstruction
```
For each shard index 0, 1, 2:
  Try primary node → if 200 OK, use it
  If primary node DOWN → check if a parity shard for that index exists
    → Fetch parity from its backup node
    → Verify checksum
  If still not found → Report "IRRECOVERABLE"
Stitch shards → serve as download
```

### 4. Integrity Checking
- Before reconstruction, re-hash every received shard
- Compare against stored SHA-256 in Metadata DB
- If mismatch → Alert: `"Data Corruption Detected in Shard [X]"`

### 5. JWT Authentication Flow
- Orchestrator requests a token from Auth Service on startup / per request
- Storage nodes validate every incoming request's `Authorization: Bearer <token>` header
- Stores use public key (shared via env var) to verify without calling auth service

---

## Dashboard UI Features
- **Upload Tab**: Drag-and-drop file upload with live progress
- **Files List**: Table showing all stored files with metadata
- **System Health**: Live status indicators for each container (Node A/B/C, Auth, DB)
- **Download**: One-click download with checksum verification status
- **Fault Demo Panel**: Button to simulate node failure + recovery animation

---

## Open Questions

> [!IMPORTANT]
> **File type support**: The project description mentions text files for simplicity, but Base64 encoding works for **any file type** (images, PDFs, etc.). I plan to support all file types via Base64. Any issues with this?

> [!IMPORTANT]
> **Prerequisite**: Docker Desktop must be installed and running on your Windows machine. Do you have Docker Desktop installed? If not, it needs to be installed first.

> [!NOTE]
> **JWT Secret**: I'll use a hardcoded secret (`SHARD_SECRET_KEY`) in `docker-compose.yml` environment variables. This is fine for a lab/demo project.

---

## Verification Plan

### Build & Start
```bash
docker-compose up --build
```

### Test Upload
```
curl -X POST http://localhost:5000/upload -F "file=@test.txt"
```

### Test Download (normal)
```
GET http://localhost:5000/download/<file_id>
```

### Test Fault Tolerance Demo
```bash
docker stop node_b
# Then attempt download from dashboard — should succeed using parity
```

### Test Corruption Detection
- Manually inject corrupt data into a shard via Node A's API
- Attempt download — dashboard should show "Data Corruption Detected"
