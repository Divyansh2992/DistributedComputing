# ShardVault — Deployment Checklist

Use this checklist to ensure your distributed system is properly configured and ready to deploy.

---

## 📋 Pre-Deployment Checklist

### Prerequisites (All 3 PCs)

- [ ] Docker installed: `docker --version` (20.10+)
- [ ] Docker Compose installed: `docker-compose --version` (1.29+)
- [ ] All 3 PCs on same LAN (can ping each other)
- [ ] Firewall allows ports 5000-5005
- [ ] All 3 PCs have static/reserved IP addresses (check your router)

### Get PC IP Addresses

**Windows:**
```bash
ipconfig
# Look for "IPv4 Address" under your network adapter
```

**Linux/macOS:**
```bash
hostname -I      # Linux
ifconfig         # macOS
```

Write down the IPs:
- [ ] PC 1 (Master): `__________` (running Orchestrator, Auth, MetaDB)
- [ ] PC 2 (Node A): `__________` (running Storage Node A)
- [ ] PC 3 (Node B+C): `__________` (running Storage Nodes B & C)

---

## 📝 Configuration Checklist

### Edit `.env` File

On **any PC**, edit the `.env` file in the project root:

```bash
# Before editing, write these values:
MASTER_IP=__________
NODE_A_IP=__________
NODE_B_IP=__________
NODE_C_IP=__________
```

Verify values in `.env`:
- [ ] `MASTER_IP` set to PC 1's IP
- [ ] `NODE_A_IP` set to PC 2's IP  
- [ ] `NODE_B_IP` set to PC 3's IP
- [ ] `NODE_C_IP` set to PC 3's IP (same as NODE_B_IP)
- [ ] `JWT_SECRET` is set (same on all PCs): `shard_secret_key_2024_distributed`

### Copy `.env` to All PCs

Make sure **identical `.env`** on all 3 PCs:

```bash
# Copy from PC 1 to PC 2
scp .env user@<PC2_IP>:/path/to/DistributedComputing/.env

# Copy from PC 1 to PC 3
scp .env user@<PC3_IP>:/path/to/DistributedComputing/.env
```

Or manually copy-paste the file to each PC.

Verify on each PC:
- [ ] PC 1: `.env` exists with correct IPs
- [ ] PC 2: `.env` exists with correct IPs
- [ ] PC 3: `.env` exists with correct IPs

---

## 🚀 Deployment Checklist

### PC 1 (Master)

```bash
cd /path/to/DistributedComputing
docker-compose up --build
```

Verify in logs:
- [ ] `orchestrator` started
- [ ] `auth_service` started
- [ ] `metadata_db` started
- [ ] No error messages
- [ ] All containers are running

**Keep this terminal running** (do not close)

### PC 2 (Node A)

```bash
cd /path/to/DistributedComputing
docker-compose -f docker-compose.node_a.yml up --build
```

Verify in logs:
- [ ] `node_a` (Storage Node A) started
- [ ] No error messages
- [ ] Container is running

**Keep this terminal running** (do not close)

### PC 3 (Nodes B + C)

```bash
cd /path/to/DistributedComputing
docker-compose -f docker-compose.node_bc.yml up --build
```

Verify in logs:
- [ ] `node_b` (Storage Node B) started
- [ ] `node_c` (Storage Node C) started
- [ ] No error messages
- [ ] Containers are running

**Keep this terminal running** (do not close)

---

## ✅ System Health Verification

### Test 1: Check Overall Health

```bash
curl http://<PC1_IP>:5000/health
```

Expected: JSON with all services "ok"
- [ ] `orchestrator` status: `ok`
- [ ] `auth_service` status: `ok`
- [ ] `metadata_db` status: `ok`
- [ ] `storage-node-0` status: `ok`
- [ ] `storage-node-1` status: `ok`
- [ ] `storage-node-2` status: `ok`

### Test 2: Upload a File

```bash
curl -X POST http://<PC1_IP>:5000/upload \
  -F "file=@/path/to/test.txt"
```

Expected: JSON response with `file_id`
- [ ] Upload returns file_id
- [ ] file_id looks like a UUID (e.g., `abc123...`)

Save the file_id:
```
file_id = __________
```

### Test 3: Download the File

```bash
curl http://<PC1_IP>:5000/download/<file_id> \
  -o /tmp/downloaded.txt
```

Expected: File downloaded successfully
- [ ] `/tmp/downloaded.txt` exists
- [ ] File size matches original file
- [ ] Content matches original file

### Test 4: List Files

```bash
curl http://<PC1_IP>:5000/files
```

Expected: JSON array with your uploaded file
- [ ] File appears in list
- [ ] File has correct metadata (filename, size, etc.)

---

## 🧪 Fault Tolerance Test

### Test Node Failure Recovery

On **PC 2** (in a new terminal), stop Storage Node B:

```bash
docker-compose -f docker-compose.node_bc.yml stop node_b
```

Verify in PC 3's terminal:
- [ ] Error logged about node_b being unavailable

Download your file again:

```bash
curl http://<PC1_IP>:5000/download/<file_id> \
  -o /tmp/recovered.txt
```

Expected: File downloaded despite node_b being down
- [ ] `/tmp/recovered.txt` exists
- [ ] Content matches original file
- [ ] Logs show XOR recovery was used

Restart the node:

```bash
docker-compose -f docker-compose.node_bc.yml start node_b
```

Verify:
- [ ] node_b comes back up in logs
- [ ] System returns to healthy state

---

## 📊 Monitoring Checklist

### Prometheus Metrics

Check metrics endpoints:

```bash
# Orchestrator metrics
curl http://<PC1_IP>:5000/metrics

# Node A metrics
curl http://<PC2_IP>:5002/metrics

# Node B metrics
curl http://<PC3_IP>:5003/metrics

# Node C metrics
curl http://<PC3_IP>:5004/metrics
```

Expected: Prometheus exposition format text
- [ ] All endpoints return metrics successfully
- [ ] Metrics show shard counts, request counts, etc.

### View Logs

```bash
# On PC 1 (in another terminal)
docker logs -f orchestrator

# On PC 2 (in another terminal)
docker logs -f node_a

# On PC 3 (in another terminal)
docker logs -f node_b
```

Verify:
- [ ] Logs show upload/download activities
- [ ] No error messages in logs
- [ ] Timestamps show recent activity

---

## 🎯 UI Access

### Open Web Dashboard

Open browser and go to:
```
http://<PC1_IP>:5000
```

Expected: Beautiful dashboard appears
- [ ] Dashboard loads (no blank page)
- [ ] "Upload File" button visible
- [ ] System health displayed
- [ ] Can select and upload files from UI

---

## 🔧 Advanced Verification

### Check Network Connectivity

From **PC 1**, verify you can reach all nodes:

```bash
ping <PC2_IP>
ping <PC3_IP>

# Test specific ports
curl http://<PC2_IP>:5002/health
curl http://<PC3_IP>:5003/health
curl http://<PC3_IP>:5004/health
```

Verify:
- [ ] All pings successful
- [ ] All health endpoints respond

### Verify Data Persistence

Upload a large file:

```bash
dd if=/dev/urandom bs=1M count=10 of=/tmp/test_large.bin
curl -X POST http://<PC1_IP>:5000/upload \
  -F "file=@/tmp/test_large.bin"
```

Note the file_id, then:

**On PC 2**, check storage:

```bash
docker exec node_a ls -lah /data/shards/
```

Verify:
- [ ] Shard file exists (about 3.3 MB for 10 MB file)
- [ ] File is readable

**On PC 3**, check storage:

```bash
docker exec node_b ls -lah /data/shards/
docker exec node_c ls -lah /data/shards/
```

Verify:
- [ ] Shard files exist on both nodes
- [ ] All files are readable

---

## 📋 Final Verification Checklist

### Core Functionality
- [ ] All 6 containers running (3 on PC 1, 1 on PC 2, 2 on PC 3)
- [ ] All services report "ok" status
- [ ] Health endpoint returns full system status
- [ ] Can upload files via UI
- [ ] Can upload files via curl
- [ ] Can download files
- [ ] Can download with one node down (XOR recovery)
- [ ] Files recovered match original files

### Networking
- [ ] PC 1 can reach PC 2 (ping works)
- [ ] PC 1 can reach PC 3 (ping works)
- [ ] All ports 5000-5005 accessible from localhost and remote PCs

### Persistence
- [ ] Shards stored in Docker volumes
- [ ] Can restart containers without data loss
- [ ] Metadata database persists

### Configuration
- [ ] `.env` identical on all 3 PCs
- [ ] All IP addresses correct in `.env`
- [ ] JWT_SECRET identical on all PCs

### Monitoring
- [ ] All `/metrics` endpoints respond
- [ ] Logs are verbose and informative
- [ ] No error messages in logs

---

## 📞 Troubleshooting Quick Reference

| Issue | Solution |
|-------|----------|
| "Connection refused" on 5000 | PC 1 containers not running; check `docker-compose up` |
| "Cannot reach Node A" | Check PC 2 IP in `.env`, verify firewall allows 5002 |
| "Upload fails with 503" | One or more nodes down; check health status |
| ".env not found" | Make sure you're in `/path/to/DistributedComputing` |
| "File not in list after upload" | Metadata DB may be down; check logs |
| "Download fails but nodes are up" | Check Docker volumes; verify `.env` IPs match actual PCs |

For more help, see [DEPLOYMENT.md](DEPLOYMENT.md) troubleshooting section.

---

## ✨ Success Criteria

Your deployment is **successful** when:

1. ✅ All 3 PCs running docker-compose without errors
2. ✅ Health endpoint shows all services "ok"
3. ✅ Can upload file from UI
4. ✅ Can download file and contents match original
5. ✅ Can stop one node and still download (XOR recovery works)
6. ✅ Prometheus metrics available on all endpoints

**If all above are checked: You're done! 🎉**

---

## 🚀 Next Steps

Once deployment is verified:

1. **Run test suite:**
   ```bash
   python3 tests/test_xor_parity.py
   python3 tests/recovery_suite.py --host <PC1_IP> --port 5000
   ```

2. **Read the architecture docs:**
   - [README.md](README.md) — System design and concepts
   - [DEPLOYMENT.md](DEPLOYMENT.md) — Full deployment guide
   - [KUBERNETES_REMOVAL_SUMMARY.md](KUBERNETES_REMOVAL_SUMMARY.md) — Changes made

3. **Experiment with the system:**
   - Upload various file sizes
   - Test recovery scenarios
   - Monitor metrics
   - Check logs

4. **For production:**
   - See [DEPLOYMENT.md](DEPLOYMENT.md) "Production Deployment" section
   - Consider Kubernetes for scaling
   - Enable HTTPS/TLS
   - Set up proper backups

---

*Last Updated: 2024-04-16 | Docker-based Multi-PC ShardVault*
