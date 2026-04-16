# K3s Multi-Laptop Setup Guide

> Complete step-by-step guide to deploy the Distributed Storage System across **3 laptops** on the same LAN using K3s.

---

## Prerequisites Checklist

Before you start, confirm all of this on **every laptop**:

| Check | Requirement |
|-------|-------------|
| ✅ | All 3 laptops on the **same Wi-Fi / LAN** |
| ✅ | Ubuntu / Debian Linux — OR — Windows with WSL2 Ubuntu |
| ✅ | Docker installed (`docker --version`) |
| ✅ | You know each laptop's IP address (`ip addr` or `ipconfig`) |
| ✅ | Internet access (for first-time K3s install only) |

---

## Step 0 — Find IP Addresses

Run on each laptop and note down the IP:

```bash
# Linux / WSL2
ip addr show | grep "inet " | grep -v 127

# Windows (in PowerShell)
ipconfig | findstr "IPv4"
```

Example (yours will differ):
- **Laptop 1 (Master):** `192.168.1.100`
- **Laptop 2 (Worker):** `192.168.1.101`  
- **Laptop 3 (Worker):** `192.168.1.102`

---

## Step 1 — Install K3s on Laptop 1 (Master)

> Run this **only on Laptop 1**

```bash
# Install K3s server (master/control-plane)
curl -sfL https://get.k3s.io | sh -

# Wait ~30 seconds, then verify it's running:
sudo kubectl get nodes
# You should see Laptop 1 listed as "Ready"
```

**Get the join token** (you'll need this for Laptops 2 and 3):
```bash
sudo cat /var/lib/rancher/k3s/server/node-token
# Copy this token — you'll use it in Steps 2 and 3
```

---

## Step 2 — Join Laptop 2 as Worker Node

> Run this **only on Laptop 2**

```bash
# Replace values with your actual IPs/token
curl -sfL https://get.k3s.io | K3S_URL=https://192.168.1.100:6443 \
  K3S_TOKEN=<PASTE_TOKEN_FROM_STEP_1> \
  sh -
```

Verify from **Laptop 1**:
```bash
sudo kubectl get nodes
# You should now see 2 nodes: Laptop 1 and Laptop 2
```

---

## Step 3 — Join Laptop 3 as Worker Node

> Run this **only on Laptop 3**

```bash
curl -sfL https://get.k3s.io | K3S_URL=https://192.168.1.100:6443 \
  K3S_TOKEN=<PASTE_TOKEN_FROM_STEP_1> \
  sh -
```

Verify from **Laptop 1**:
```bash
sudo kubectl get nodes
# You should now see ALL 3 nodes listed as "Ready"
```

---

## Step 4 — Label the Nodes

K8s uses labels to pin pods to specific machines. Run from **Laptop 1**:

```bash
# Find the exact node names
sudo kubectl get nodes

# Label each node (replace node1/node2/node3 with your actual node names)
sudo kubectl label node <LAPTOP-1-HOSTNAME> laptop=laptop-1
sudo kubectl label node <LAPTOP-2-HOSTNAME> laptop=laptop-2
sudo kubectl label node <LAPTOP-3-HOSTNAME> laptop=laptop-3

# Verify labels
sudo kubectl get nodes --show-labels
```

> **Tip:** Your hostname is usually the output of `hostname` command on each laptop.

---

## Step 5 — Set Up kubectl on Laptop 1 (without sudo)

```bash
mkdir -p ~/.kube
sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown $(id -u):$(id -g) ~/.kube/config

# Now you can use kubectl without sudo:
kubectl get nodes
```

---

## Step 6 — Configure Insecure Registry on All 3 Laptops

Because we use a **local HTTP registry** (not HTTPS), we must tell Docker/K3s to trust it.

**On ALL 3 laptops**, replace `192.168.1.100` with your actual Laptop 1 IP:

### For Docker (if installed):
```bash
# Edit or create /etc/docker/daemon.json
sudo tee /etc/docker/daemon.json <<EOF
{
  "insecure-registries": ["192.168.1.100:5050"]
}
EOF
sudo systemctl restart docker
```

### For K3s (containerd):
```bash
# Create registries config for K3s
sudo mkdir -p /etc/rancher/k3s
sudo tee /etc/rancher/k3s/registries.yaml <<EOF
mirrors:
  "192.168.1.100:5050":
    endpoint:
      - "http://192.168.1.100:5050"
EOF

# Restart K3s agent to apply (on worker nodes)
sudo systemctl restart k3s-agent

# On master (Laptop 1):
sudo systemctl restart k3s
```

---

## Step 7 — Build & Push All Images (Laptop 1 only)

> Go to the project root on Laptop 1

```bash
cd /path/to/DistributedComputing

# Make the script executable
chmod +x k8s/build-and-push.sh

# Run it with Laptop 1's IP address
./k8s/build-and-push.sh 192.168.1.100
```

This script will:
1. Start a local Docker registry on port `5050`
2. Build all 4 Docker images (`orchestrator`, `auth-service`, `metadata-db`, `storage-node`)
3. Push them to the local registry
4. Automatically patch the `REGISTRY_IP` placeholder in all YAML files

---

## Step 8 — Deploy Everything

From **Laptop 1**, apply all manifests in order:

```bash
cd /path/to/DistributedComputing

# 1. Create the namespace first
kubectl apply -f k8s/namespace.yaml

# 2. Apply secrets and config
kubectl apply -f k8s/secret.yaml
kubectl apply -f k8s/configmap.yaml

# 3. Deploy all services
kubectl apply -f k8s/metadata-db.yaml
kubectl apply -f k8s/auth-service.yaml
kubectl apply -f k8s/node-a.yaml
kubectl apply -f k8s/node-b.yaml
kubectl apply -f k8s/node-c.yaml

# 4. Deploy the orchestrator last (depends on all others)
kubectl apply -f k8s/orchestrator.yaml
```

Wait for all pods to be **Running**:
```bash
watch kubectl get pods -n distributed-storage
# Press Ctrl+C when all show STATUS=Running
```

---

## Step 9 — Verify Deployment

```bash
# See all pods and WHICH LAPTOP they're running on
kubectl get pods -n distributed-storage -o wide

# Expected output:
# NAME                         READY  STATUS   NODE
# orchestrator-xxx             1/1    Running  laptop-1-hostname
# metadata-db-xxx              1/1    Running  laptop-1-hostname
# auth-service-xxx             1/1    Running  laptop-2-hostname
# node-a-xxx                   1/1    Running  laptop-2-hostname
# node-b-xxx                   1/1    Running  laptop-3-hostname
# node-c-xxx                   1/1    Running  laptop-3-hostname

# Check services
kubectl get services -n distributed-storage
# orchestrator should show TYPE=NodePort, PORT=5000:30000/TCP
```

---

## Step 10 — Access the Dashboard

Open a browser on **ANY laptop** (or phone on the same Wi-Fi):

```
http://192.168.1.100:30000
```

> Replace `192.168.1.100` with your actual Laptop 1 IP.  
> The NodePort `30000` is exposed on ALL nodes, so even `http://192.168.1.101:30000` works!

---

## Useful Commands Reference

```bash
# Watch all pods live
watch kubectl get pods -n distributed-storage -o wide

# See logs of a specific pod
kubectl logs -f <pod-name> -n distributed-storage

# Restart a deployment
kubectl rollout restart deployment/orchestrator -n distributed-storage

# Simulate a node failure (delete a pod — K8s auto-recreates it):
kubectl delete pod <node-a-pod-name> -n distributed-storage

# Scale a deployment
kubectl scale deployment node-a --replicas=2 -n distributed-storage

# Describe a pod (debug crashes)
kubectl describe pod <pod-name> -n distributed-storage

# Remove ALL resources (clean up)
kubectl delete namespace distributed-storage
```

---

## Firewall Rules (if pods can't connect)

Run on **ALL laptops**:

```bash
# K3s API server (master only)
sudo ufw allow 6443/tcp

# Kubelet agent communication
sudo ufw allow 10250/tcp

# Dashboard NodePort (all nodes)
sudo ufw allow 30000/tcp

# Local Docker registry (Laptop 1 only)
sudo ufw allow 5050/tcp

# K3s inter-node communication
sudo ufw allow 8472/udp
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Pod stuck in `Pending` | Check labels: `kubectl describe pod <name> -n distributed-storage` — look for "no nodes match NodeSelector" |
| `ImagePullBackOff` | Registry unreachable — check `/etc/rancher/k3s/registries.yaml` and restart K3s |
| Worker not joining | Check firewall on Laptop 1 (port 6443), verify the token is correct |
| Dashboard not loading | Verify NodePort with `kubectl get svc -n distributed-storage` |
| Pods crashing | Check logs: `kubectl logs <pod> -n distributed-storage` |

---

## Architecture Summary

```
http://<ANY-laptop-IP>:30000
            │
            ▼
   ┌──────────────────┐        ┌──────────────────┐
   │   LAPTOP 1       │        │   LAPTOP 2       │
   │  orchestrator    │──JWT──►│  auth-service    │
   │  metadata-db     │◄──────►│  node-a (Shard0) │
   └──────────────────┘        └──────────────────┘
            │                           
            │                  ┌──────────────────┐
            └─────────────────►│   LAPTOP 3       │
                               │  node-b (Shard1) │
                               │  node-c (Parity) │
                               └──────────────────┘
```

Kubernetes handles **all routing transparently** — from your browser's perspective, it's just one service at one URL. 🎉
