# Kubernetes Multi-Laptop Distributed Deployment Plan

## Goal
Take your existing 6-container Docker-based distributed storage system and deploy it across **3 physical laptops** connected on the same LAN using **Kubernetes (K3s)**. From any laptop, you access ONE IP and everything works transparently — Kubernetes handles routing, load balancing, and container placement automatically.

---

## Architecture Overview

```
         YOUR LAN (e.g., 192.168.1.x)
         ┌──────────────────────────────────────────────────────┐
         │                                                      │
   ┌─────▼──────┐        ┌────────────┐        ┌────────────┐  │
   │ Laptop 1   │        │ Laptop 2   │        │ Laptop 3   │  │
   │ MASTER     │        │ WORKER     │        │ WORKER     │  │
   │ (Control   │◄──────►│ Node       │◄──────►│ Node       │  │
   │  Plane)    │        │            │        │            │  │
   │            │        │ Runs:      │        │ Runs:      │  │
   │ Runs:      │        │ - node_a   │        │ - node_b   │  │
   │ - orchestr │        │ - auth_svc │        │ - node_c   │  │
   │ - metadata │        │            │        │            │  │
   │            │        │            │        │            │  │
   └────────────┘        └────────────┘        └────────────┘
          │
          │  NodePort :30000
          ▼
   http://<Laptop1-IP>:30000   ← Single access point for everyone
```

**K3s** (lightweight Kubernetes) runs on all 3 laptops. Laptop 1 is the **master**, the others are **workers**. K3s automatically distributes your containers (Pods) across all nodes.

---

## User Review Required

> [!IMPORTANT]
> **Prerequisites on ALL 3 laptops:**
> - Linux OS or WSL2 on Windows (K3s runs natively on Linux)
> - All 3 laptops connected to the **same Wi-Fi/LAN network**
> - Docker installed on all 3 (for building images)
> - You need the **IP address of each laptop** (run `ip addr` or `ipconfig`)
> - Ports **6443, 10250, 30000** must be unblocked (firewall rules needed)

> [!WARNING]
> **If all 3 laptops run Windows**: K3s doesn't run natively on Windows. You'll need WSL2 Ubuntu enabled on each laptop. I'll include WSL2 setup steps.

> [!NOTE]
> **Image Registry**: Since the 3 laptops share a LAN (not the internet), we'll use a **local Docker registry** on Laptop 1 so all workers can pull your custom images without pushing to Docker Hub.

---

## Proposed Changes

### Component 1: Local Docker Registry (on Laptop 1)

Run a local image registry at `<Laptop1-IP>:5050` so all nodes can pull your images.

#### [NEW] `k8s/registry/registry-deploy.sh`
Shell script to start the local registry.

---

### Component 2: Kubernetes Manifests (YAML files)

All manifests go into a new `k8s/` folder at the project root.

#### [NEW] `k8s/namespace.yaml`
Creates a dedicated `distributed-storage` Kubernetes namespace.

#### [NEW] `k8s/secret.yaml`
Stores the JWT secret as a K8s Secret (base64 encoded).

#### [NEW] `k8s/configmap.yaml`
Stores service URLs so the orchestrator knows where to reach each node.

#### [NEW] `k8s/auth-service.yaml`
Deployment + Service for `auth_service` (ClusterIP, internal only).

#### [NEW] `k8s/metadata-db.yaml`
Deployment + Service for `metadata_db` with a PersistentVolumeClaim.

#### [NEW] `k8s/node-a.yaml`
Deployment + Service for `node_a` (storage) with PVC, pinned to **Laptop 2**.

#### [NEW] `k8s/node-b.yaml`
Deployment + Service for `node_b` (storage) with PVC, pinned to **Laptop 3**.

#### [NEW] `k8s/node-c.yaml`
Deployment + Service for `node_c` (parity storage) with PVC, pinned to **Laptop 3**.

#### [NEW] `k8s/orchestrator.yaml`
Deployment + Service for `orchestrator` exposed as **NodePort 30000** — the single public entry point.

---

### Component 3: Build & Push Script

#### [NEW] `k8s/build-and-push.sh`
Script to build all Docker images and push them to the local registry on Laptop 1. Run this once on Laptop 1.

---

### Component 4: K3s Setup Guide

#### [NEW] `k8s/SETUP.md`
Step-by-step setup guide with exact commands for:
1. Installing K3s on Laptop 1 (master)
2. Joining Laptop 2 & 3 as workers
3. Labeling nodes for pod placement
4. Deploying all manifests
5. Accessing the dashboard

---

## Verification Plan

### Automated Tests
```bash
# Check all pods are running
kubectl get pods -n distributed-storage

# Check which laptop each pod runs on
kubectl get pods -n distributed-storage -o wide

# Access the dashboard from ANY laptop on the network
http://<Laptop1-IP>:30000
```

### Manual Verification
1. Open `http://<Laptop1-IP>:30000` on **Laptop 2 or 3** (not the master) — dashboard should load
2. Upload a file — it gets sharded across Node A (Laptop 2), Node B (Laptop 3), Node C (Laptop 3)
3. Download the file — reconstructed transparently
4. **Kill a pod** on one worker: `kubectl delete pod <node-a-pod> -n distributed-storage` → K8s auto-restarts it on the same node
5. System health panel in dashboard should show all green

---

## File Layout After Changes

```
DistributedComputing/
├── docker-compose.yml         (unchanged, local dev)
├── orchestrator/              (unchanged)
├── storage_node/              (unchanged)  
├── auth_service/              (unchanged)
├── metadata_db/               (unchanged)
└── k8s/                       ← NEW FOLDER
    ├── SETUP.md               ← Step-by-step guide
    ├── build-and-push.sh      ← Build images + push to local registry
    ├── namespace.yaml
    ├── secret.yaml
    ├── configmap.yaml
    ├── auth-service.yaml
    ├── metadata-db.yaml
    ├── node-a.yaml
    ├── node-b.yaml
    ├── node-c.yaml
    └── orchestrator.yaml
```
