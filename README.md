# 🔷 ShardVault — Distributed Sharded Storage with Fault Tolerance

> **BTech Distributed Computing Project** — A 6-container Docker system that shreds files into shards, distributes them across nodes, and reconstructs them on demand with parity-based fault tolerance and SHA-256 integrity checking.

---

## 📐 Architecture

```
User Browser (Dashboard at :5000)
        │
        ▼
┌─────────────────────┐
│   Orchestrator      │  :5000  ← Master logic: shard / reconstruct / health
└──┬──┬──┬──┬─────────┘
   │  │  │  │
   ▼  │  │  ▼
Auth  │  │  Metadata DB  :5005  (SQLite — stores file "recipes")
:5001 │  │
      ▼  ▼
   Node A  Node B  Node C
   :5002   :5003   :5004
  Shard 0 Shard 1  Shard 2 + Parity(1)
```

### The 6 Containers

| Container    | Port | Role |
|---|---|---|
| `orchestrator`  | 5000 | Master — splits, distributes, reconstructs |
| `auth_service`  | 5001 | JWT gatekeeper — issues & validates tokens |
| `node_a`        | 5002 | Storage node — holds Shard 0 |
| `node_b`        | 5003 | Storage node — holds Shard 1 |
| `node_c`        | 5004 | Storage node — holds Shard 2 + Parity of Shard 1 |
| `metadata_db`   | 5005 | SQLite REST API — stores file recipes + SHA-256 hashes |

---

## 🚀 Running the Project

### Prerequisites
- Docker Desktop (running)
- That's it.

### Start all 6 containers
```bash
docker-compose up --build
```

### Open the dashboard
```
http://localhost:5000
```

### Stop everything
```bash
docker-compose down
```

### Stop everything + wipe all stored data
```bash
docker-compose down -v
```

---

## 🔑 Key Features

### 1. File Sharding (Any File Type)
- File bytes are Base64-encoded
- Split into **3 equal shards**: `S0 → Node A`, `S1 → Node B`, `S2 → Node C`
- A **4th parity shard** (copy of S1) is also stored on Node C
- Each shard's **SHA-256 hash** is stored in Metadata DB

### 2. JWT Authentication
- Orchestrator requests a token from Auth Service before every node interaction
- Storage nodes **verify the JWT** (shared secret) on every request
- Unauthorized requests are rejected with `401`

### 3. Fault-Tolerant Reconstruction
- When downloading, Orchestrator fetches all 3 primary shards in order
- If a node is **DOWN** → falls back to the **parity shard** on Node C
- If parity is also unavailable → returns `IRRECOVERABLE`

### 4. SHA-256 Integrity Checking
- Before stitching shards, each received chunk is re-hashed
- If hash **doesn't match** stored value → `[CORRUPT] Data Corruption Detected`

---

## 🧪 Demo Scenarios

### A) Normal Upload & Download
1. Open `http://localhost:5000`
2. Drag & drop any file
3. Click **Download** — file reconstructed from 3 shards

### B) Node Failure + Recovery (Fault Tolerance)
```bash
# Kill Node B (holds Shard 1)
docker stop node_b

# Download via dashboard — Orchestrator recovers Shard 1 from parity on Node C
# Activity log will show: [RECOVERY] Shard 1 recovered via parity on node_c

# Restore Node B
docker start node_b
```

### C) Corruption Detection (Integrity Check)
1. Upload a file
2. Click **⚠ Corrupt S0** in the files table
3. Download the file — log shows `[CORRUPT] Shard 0 checksum MISMATCH!`

---

## 📡 REST API Reference

| Method | Endpoint | Description |
|---|---|---|
| `GET`  | `/health` | System health of all 6 containers |
| `POST` | `/upload` | Upload a file (multipart/form-data) |
| `GET`  | `/files` | List all stored files |
| `GET`  | `/download/<file_id>` | Reconstruct & download file |
| `DELETE` | `/files/<file_id>` | Delete file and all its shards |
| `POST` | `/demo/corrupt/<file_id>/<n>` | Inject corruption into shard N (demo) |

---

## 📁 Project Structure

```
Project/
├── docker-compose.yml
├── README.md
├── auth_service/          # JWT issuer & verifier
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app.py
├── storage_node/          # Shared codebase for Node A, B, C
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app.py
├── metadata_db/           # SQLite REST API
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app.py
└── orchestrator/          # Master logic + dashboard
    ├── Dockerfile
    ├── requirements.txt
    ├── app.py
    └── static/
        └── index.html     # Web dashboard
```
