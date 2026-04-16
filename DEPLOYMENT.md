# ShardVault — Docker Deployment Guide

## Overview

ShardVault is a **distributed file storage system** that runs on **multiple PCs** using **Docker**. Change **one `.env` file** with IP addresses, and the entire system works across your LAN.

---

## Prerequisites

### Required on each PC:
- **Docker** (version 20.10+): [Install Docker](https://docs.docker.com/get-docker/)
- **Docker Compose** (version 1.29+): Usually included with Docker Desktop
- **Python 3.11+** (for running test scripts, optional)

### Network:
- All PCs must be on the **same LAN** (able to ping each other)
- **Firewall** must allow TCP ports: **5000-5005**
- All PCs must have **static or reserved LAN IP addresses** (DHCP can change IPs)

---

## Quick Start (3 PCs)

### Step 1: Identify Your LAN IPs

**Windows:**
```powershell
ipconfig
# Look for "IPv4 Address" under your active network adapter
# Example: 192.168.1.100
```

**Linux/macOS:**
```bash
hostname -I      # Linux
ifconfig         # macOS
# Example: 192.168.1.100
```

### Step 2: Edit `.env` File

On **any PC** (you'll copy it to all 3 later), edit `.env`:

```bash
# PC 1 (Master) — runs Orchestrator, Auth Service, Metadata DB
MASTER_IP=192.168.1.100

# PC 2 (Storage Node A) — runs shard_0
NODE_A_IP=192.168.1.101

# PC 3 (Storage Nodes B+C) — runs shard_1 and shard_2+parity
NODE_B_IP=192.168.1.102
NODE_C_IP=192.168.1.102

JWT_SECRET=shard_secret_key_2024_distributed  # Keep identical on all PCs
```

### Step 3: Copy `.env` to All PCs

Make sure the **exact same `.env` file** is on all 3 PCs in the root of the project directory.

```bash
# Copy to PC 2 and PC 3
scp .env user@<PC2_IP>:/path/to/DistributedComputing/.env
scp .env user@<PC3_IP>:/path/to/DistributedComputing/.env
```

### Step 4: Start Services

#### On **PC 1** (Master — 192.168.1.100):
```bash
cd /path/to/DistributedComputing
docker-compose up --build
```

**Expected output:**
```
orchestrator | WARNING in app.run(): This is a development server. Do not use it in production deployments.
auth_service | WARNING in app.run(): This is a development server. Do not use it in production deployments.
metadata_db  | WARNING in app.run(): This is a development server. Do not use it in production deployments.
```

#### On **PC 2** (Node A — 192.168.1.101):
```bash
cd /path/to/DistributedComputing
docker-compose -f docker-compose.node_a.yml up --build
```

#### On **PC 3** (Node B+C — 192.168.1.102):
```bash
cd /path/to/DistributedComputing
docker-compose -f docker-compose.node_bc.yml up --build
```

### Step 5: Verify Everything Works

Open your browser and go to:
```
http://192.168.1.100:5000
```

**You should see the ShardVault dashboard.**

---

## Alternative: 2-PC Setup

If you only have 2 PCs:

**Edit `.env`:**
```env
MASTER_IP=192.168.1.100      # PC 1
NODE_A_IP=192.168.1.101      # PC 2
NODE_B_IP=192.168.1.101      # PC 2 (same as NODE_A)
NODE_C_IP=192.168.1.101      # PC 2 (same as NODE_A)
```

**PC 1:** Run `docker-compose up --build`
**PC 2:** Run both:
```bash
docker-compose -f docker-compose.node_a.yml up --build
docker-compose -f docker-compose.node_bc.yml up --build
```

(Or run them in separate terminals)

---

## How It Works (Architecture)

### Services by PC:

| PC | Service | Port | Purpose |
|---|---|---|---|
| **PC 1** | Orchestrator | 5000 | Main API + Web UI |
| **PC 1** | Auth Service | 5001 | JWT token issuing |
| **PC 1** | Metadata DB | 5005 | File recipes + SQLite |
| **PC 2** | Storage Node A | 5002 | Stores shard_0 |
| **PC 3** | Storage Node B | 5003 | Stores shard_1 |
| **PC 3** | Storage Node C | 5004 | Stores shard_2 + parity |

### Communication Flow:

```
Browser (http://192.168.1.100:5000)
         ↓
    Orchestrator (PC 1:5000)
         ├→ Auth Service (PC 1:5001) — get JWT token
         ├→ Metadata DB (PC 1:5005) — store file recipe
         ├→ Storage Node A (PC 2:5002) — write shard_0
         ├→ Storage Node B (PC 3:5003) — write shard_1
         └→ Storage Node C (PC 3:5004) — write shard_2 + parity
```

**Key:** All communication uses **LAN IP addresses** from `.env` file. No DNS needed.

---

## Usage Examples

### Upload a File

**Using the Web UI:**
1. Open http://192.168.1.100:5000
2. Click "Upload File"
3. Select a file → Click "Upload"
4. File is split into 3 shards + parity, distributed across all 3 storage nodes

**Using curl:**
```bash
curl -X POST -F "file=@myfile.txt" \
  http://192.168.1.100:5000/upload
```

### List Files

```bash
curl http://192.168.1.100:5000/files
```

### Download a File

```bash
curl http://192.168.1.100:5000/download/<FILE_ID> \
  -o recovered_file.txt
```

### Check System Health

```bash
curl http://192.168.1.100:5000/health
```

Response:
```json
{
  "orchestrator": {"status": "ok", "service": "orchestrator"},
  "auth_service": {"status": "ok", "service": "auth_service"},
  "metadata_db": {"status": "ok", "service": "metadata_db"},
  "nodes": {
    "storage-node-0": {"status": "ok", "node": "storage-node-0", "shard_count": 42, "free_gb": 50.5},
    "storage-node-1": {"status": "ok", "node": "storage-node-1", "shard_count": 42, "free_gb": 49.2},
    "storage-node-2": {"status": "ok", "node": "storage-node-2", "shard_count": 42, "free_gb": 48.8}
  }
}
```

---

## Troubleshooting

### "Connection refused" on port 5000

**Problem:** Orchestrator isn't running

**Solution:**
```bash
# On PC 1, check Docker logs
docker logs orchestrator

# Restart if needed
docker-compose down
docker-compose up --build
```

### "Node A not responding" error on upload

**Problem:** Storage node on PC 2 isn't reachable

**Solution:**
1. Check PC 2's IP in `.env` matches actual IP
2. Verify firewall allows port 5002
3. Ping PC 2 from PC 1: `ping 192.168.1.101`
4. Check Docker container is running on PC 2: `docker ps`

### All services running but files don't upload

**Problem:** Likely misconfigured `.env` (IPs don't match actual PCs)

**Solution:**
1. Verify each PC's actual LAN IP: `ipconfig` / `hostname -I`
2. Update `.env` with correct IPs
3. Copy updated `.env` to **all 3 PCs**
4. Restart all Docker services

### Storage Node B or C not responding

**Problem:** Storage nodes on PC 3 crashed

**Solution:**
```bash
# On PC 3
docker-compose -f docker-compose.node_bc.yml down
docker-compose -f docker-compose.node_bc.yml up --build
```

---

## Fault Tolerance

### How It Works:

Files are split into **3 shards + 1 parity shard** using XOR:
```
shard_0, shard_1, shard_2, parity = shard_0 ⊕ shard_1 ⊕ shard_2
```

**Storage Layout:**
- **Node A (PC 2:5002):** shard_0
- **Node B (PC 3:5003):** shard_1
- **Node C (PC 3:5004):** shard_2 + parity

### What Happens if a Node Fails?

| Node Fails | Can Recover? | Reason |
|---|---|---|
| **Node A (PC 2)** | ✅ YES | Have shard_1, shard_2, parity → recover shard_0 |
| **Node B (PC 3)** | ✅ YES | Have shard_0, shard_2, parity → recover shard_1 |
| **Node C (PC 3)** | ❌ NO | Parity is on Node C; if it fails, parity is lost |

**Best Practice:** Node C should be on the most reliable PC (not the one that crashes often).

### Automatic Recovery:

When you download a file and a shard is missing:
1. Orchestrator fetches remaining shards
2. Computes missing shard using XOR formula
3. Returns complete file to you
4. SHA-256 checksums verify integrity

---

## Monitoring & Observability

### Prometheus Metrics

Each service exposes `/metrics` endpoint:

```bash
# Orchestrator metrics
curl http://192.168.1.100:5000/metrics

# Storage Node A metrics
curl http://192.168.1.101:5002/metrics

# Storage Node B metrics
curl http://192.168.1.102:5003/metrics
```

### View Logs

```bash
# On the PC where the service is running
docker logs <container_name>

# Examples:
docker logs orchestrator
docker logs node_a
docker logs auth_service
docker logs metadata_db
```

### Live tail logs:
```bash
docker logs -f orchestrator
```

---

## Advanced: Scaling to 4+ Nodes

Want to add a 4th storage node for even better fault tolerance?

1. **Update `.env`:**
```env
MASTER_IP=192.168.1.100
NODE_A_IP=192.168.1.101
NODE_B_IP=192.168.1.102
NODE_C_IP=192.168.1.103      # 4th PC
```

2. **Update `orchestrator/app.py`:**
   Change the NODES dictionary to include more nodes and adjust sharding logic

3. **Restart services**

---

## Performance Tuning

### Adjust Gunicorn Workers

Edit each `Dockerfile` to increase workers:

**For orchestrator (handles many uploads):**
```dockerfile
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "4", "--timeout", "120", "app:app"]
```

**For storage nodes (I/O heavy):**
```dockerfile
CMD ["gunicorn", "--bind", "0.0.0.0:${PORT:-5002}", "--workers", "4", "--timeout", "60", "app:app"]
```

Then rebuild: `docker-compose up --build`

### Increase metadata database WAL timeout

Edit `metadata_db/app.py`:
```python
conn.execute('PRAGMA busy_timeout = 5000')  # 5 seconds instead of 30
```

---

## Production Deployment

⚠️ **This guide is for LAN/testing only.**

For production:
1. Use **fixed IP addresses** (not DHCP)
2. Use a **reverse proxy** (nginx, Traefik) instead of direct Docker ports
3. Enable **HTTPS/TLS** (Let's Encrypt)
4. Store secrets in environment variables, not `.env` files
5. Use **Docker secrets** or **Kubernetes** for orchestration
6. Set up proper **backups** of metadata DB
7. Monitor with **Prometheus + Grafana**

---

## Clean Up

### Stop all services:

**On PC 1:**
```bash
docker-compose down
docker-compose down -v  # Also remove volumes
```

**On PC 2:**
```bash
docker-compose -f docker-compose.node_a.yml down
```

**On PC 3:**
```bash
docker-compose -f docker-compose.node_bc.yml down
```

### Remove all Docker images:
```bash
docker system prune -a
```

---

## Common Commands Cheat Sheet

```bash
# Start services
docker-compose up --build

# Stop services
docker-compose down

# View logs
docker logs <container_name>
docker logs -f <container_name>

# List running containers
docker ps

# Rebuild images without cache
docker-compose build --no-cache

# View resource usage
docker stats

# Execute command in container
docker exec <container_name> ls -la /data/shards

# Remove unused images/volumes
docker system prune -a --volumes
```

---

## Help & Support

If you encounter issues:

1. **Check logs:** `docker logs <service_name>`
2. **Verify network:** `ping <other_pc_ip>`
3. **Test ports:** `curl http://<ip>:<port>/health`
4. **Review `.env`:** Make sure all IPs are correct and identical on all PCs
5. **Rebuild:** `docker-compose down && docker-compose build --no-cache && docker-compose up`

---

## Next Steps

- ✅ **Deployment complete!** Start using ShardVault
- 🧪 Run test suite: `python tests/recovery_suite.py`
- 📊 Set up monitoring with Prometheus + Grafana
- 🔒 Enable HTTPS for production
- 📖 Read [README.md](README.md) for architecture details
