# 🚀 Real 2-Machine Deployment Guide
> **Your exact setup:** Machine A (you, with all code) + Machine B (non-technical user) + Mobile → All on same mobile hotspot

---

## 📋 What Will Run Where

```
MOBILE HOTSPOT  (e.g., 192.168.90.x)
├── Machine A (YOUR LAPTOP) — K3s MASTER
│   ├── orchestrator     ← Port 30000 (public entry point)
│   ├── metadata_db      ← stores file metadata
│   ├── node_a           ← stores shard 0
│   └── auth_service     ← handles login/JWT
│
└── Machine B (OTHER LAPTOP) — K3s WORKER
    ├── node_b            ← stores shard 1
    └── node_c            ← stores parity shard
```

**Access from ANYWHERE on hotspot:**
```
http://<Machine-A-IP>:30000
```

---

## ⚡ STEP 0 — Find Your IPs (Both Machines)

### On Machine A (yours) — Windows PowerShell:
```powershell
ipconfig | findstr "IPv4"
```
Note the IP that starts with `192.168.` — this is your **Machine A IP**
(e.g., `192.168.90.10`)

### On Machine B — Windows PowerShell:
```powershell
ipconfig | findstr "IPv4"
```
Note that IP too (e.g., `192.168.90.20`)

> 📌 Write these down — you'll need them throughout.

---

## 🪟 STEP 1 — Enable WSL2 on BOTH Machines

K3s runs on Linux. On Windows, we use WSL2 (Windows Subsystem for Linux).

### On Machine A — Run in PowerShell AS ADMINISTRATOR:
```powershell
wsl --install
```
If already installed, update it:
```powershell
wsl --update
wsl --set-default-version 2
```
Then install Ubuntu from Microsoft Store OR:
```powershell
wsl --install -d Ubuntu-22.04
```
**Reboot if prompted.**

After reboot, open Ubuntu (search "Ubuntu" in Start menu) and set up a username/password.

### On Machine B — same commands:
```powershell
# Run in PowerShell as ADMIN on Machine B too
wsl --install -d Ubuntu-22.04
```
**Reboot Machine B if prompted.** Then open Ubuntu on Machine B.

> ⚠️ From this point, ALL commands run inside Ubuntu/WSL2 terminal (not PowerShell)

---

## 🐳 STEP 2 — Install Docker in WSL2 (Machine A ONLY)

Open Ubuntu terminal on **Machine A**:

```bash
# Update packages
sudo apt-get update && sudo apt-get upgrade -y

# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Allow running Docker without sudo
sudo usermod -aG docker $USER
newgrp docker

# Verify Docker works
docker --version
```

---

## 🔧 STEP 3 — Install K3s on Machine A (MASTER)

Open Ubuntu terminal on **Machine A**:

```bash
# Install K3s as master node
# IMPORTANT: Replace 192.168.90.10 with YOUR actual Machine A IP
curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="--node-external-ip=192.168.90.10 --advertise-address=192.168.90.10" sh -

# Wait 30 seconds, then check it's running:
sudo kubectl get nodes
# You should see 1 node with STATUS=Ready
```

**Get the join token (save this for Machine B):**
```bash
sudo cat /var/lib/rancher/k3s/server/node-token
```
> 📌 Copy this long token string — you'll give it to Machine B.

**Setup kubectl without sudo:**
```bash
mkdir -p ~/.kube
sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown $(id -u):$(id -g) ~/.kube/config
# Replace the localhost address with Machine A's real IP
sed -i 's/127.0.0.1/192.168.90.10/g' ~/.kube/config

# Test:
kubectl get nodes
```

---

## 🔧 STEP 4 — Install K3s on Machine B (WORKER)

Open Ubuntu terminal on **Machine B**.

First install K3s as a worker — replace the values:
- `192.168.90.10` → Machine A's actual IP
- `<TOKEN>` → the token you copied from Step 3

```bash
# On Machine B — join as worker
curl -sfL https://get.k3s.io | K3S_URL=https://192.168.90.10:6443 \
  K3S_TOKEN=<PASTE_TOKEN_FROM_MACHINE_A> \
  sh -

# Verify it's running:
sudo systemctl status k3s-agent
```

**Back on Machine A**, verify Machine B joined:
```bash
kubectl get nodes
# Should show 2 nodes, both Ready
```

---

## 🏷️ STEP 5 — Label the Nodes (Machine A)

```bash
# See node names
kubectl get nodes

# Label Machine A as laptop-a (replace <MACHINE-A-NODE-NAME> with actual name from above)
kubectl label node <MACHINE-A-NODE-NAME> laptop=laptop-a

# Label Machine B as laptop-b (replace <MACHINE-B-NODE-NAME> with actual name)
kubectl label node <MACHINE-B-NODE-NAME> laptop=laptop-b

# Verify labels
kubectl get nodes --show-labels
```

> 💡 The node name is usually the laptop's hostname (e.g., `divyansh-laptop`). 
> Run `hostname` in WSL2 on each machine to confirm.

---

## 📦 STEP 6 — Configure Local Docker Registry (Machine A)

We put a private Docker registry on Machine A so Machine B can pull images.

```bash
# Start local Docker registry on Machine A
docker run -d \
  --name local-registry \
  --restart=always \
  -p 5050:5000 \
  registry:2

# Verify it's running:
docker ps | grep registry
```

**Tell K3s to trust this registry — On Machine A:**
```bash
sudo mkdir -p /etc/rancher/k3s

# Replace 192.168.90.10 with YOUR Machine A IP
sudo tee /etc/rancher/k3s/registries.yaml <<EOF
mirrors:
  "192.168.90.10:5050":
    endpoint:
      - "http://192.168.90.10:5050"
EOF

sudo systemctl restart k3s
```

**Tell K3s to trust this registry — On Machine B (in WSL2):**
```bash
# Replace 192.168.90.10 with Machine A IP
sudo mkdir -p /etc/rancher/k3s
sudo tee /etc/rancher/k3s/registries.yaml <<EOF
mirrors:
  "192.168.90.10:5050":
    endpoint:
      - "http://192.168.90.10:5050"
EOF

sudo systemctl restart k3s-agent
```

---

## 🏗️ STEP 7 — Build & Push Docker Images (Machine A)

Navigate to your project folder in WSL2 on Machine A.

> ⚠️ In WSL2, your Windows path `E:\Desktop\6thSem\dc\DistributedComputing` becomes `/mnt/e/Desktop/6thSem/dc/DistributedComputing`

```bash
cd /mnt/e/Desktop/6thSem/dc/DistributedComputing

# Make script executable
chmod +x k8s/build-and-push.sh

# Run it — replace 192.168.90.10 with your actual Machine A IP
./k8s/build-and-push.sh 192.168.90.10
```

This will build all 4 Docker images and push them to your local registry.
It takes 3-10 minutes depending on your internet speed (first time).

**Verify images are in registry:**
```bash
curl http://192.168.90.10:5050/v2/_catalog
# Should show: {"repositories":["auth-service","metadata-db","orchestrator","storage-node"]}
```

---

## 🔄 STEP 8 — Update YAML Files for 2-Machine Setup

The existing YAML files are configured for 3 machines. We need to adapt them for 2 machines.

Run this on Machine A to patch all YAML files with your registry IP:
```bash
cd /mnt/e/Desktop/6thSem/dc/DistributedComputing

# Replace REGISTRY_IP placeholder in all yamls
sed -i 's/REGISTRY_IP/192.168.90.10/g' k8s/*.yaml

# Patch node-b.yaml and node-c.yaml to use laptop-b label instead of laptop-3
sed -i 's/laptop-3/laptop-b/g' k8s/node-b.yaml
sed -i 's/laptop-3/laptop-b/g' k8s/node-c.yaml

# Patch node-a.yaml and others to use laptop-a label
sed -i 's/laptop-2/laptop-a/g' k8s/node-a.yaml
sed -i 's/laptop-1/laptop-a/g' k8s/orchestrator.yaml
sed -i 's/laptop-1/laptop-a/g' k8s/metadata-db.yaml
sed -i 's/laptop-1/laptop-a/g' k8s/auth-service.yaml
```

---

## 🚀 STEP 9 — Deploy Everything (Machine A)

```bash
cd /mnt/e/Desktop/6thSem/dc/DistributedComputing

# 1. Create namespace
kubectl apply -f k8s/namespace.yaml

# 2. Apply config
kubectl apply -f k8s/secret.yaml
kubectl apply -f k8s/configmap.yaml

# 3. Deploy services
kubectl apply -f k8s/metadata-db.yaml
kubectl apply -f k8s/auth-service.yaml
kubectl apply -f k8s/node-a.yaml
kubectl apply -f k8s/node-b.yaml
kubectl apply -f k8s/node-c.yaml

# 4. Deploy orchestrator last
kubectl apply -f k8s/orchestrator.yaml
```

**Watch pods come up (wait for all to be Running):**
```bash
watch kubectl get pods -n distributed-storage -o wide
# Press Ctrl+C when all show STATUS=Running
# node-b and node-c should show on Machine B's node name
```

---

## 🔥 STEP 10 — Open Firewall Ports (Machine A — Windows)

Run in **Windows PowerShell as Admin** on Machine A:

```powershell
# K3s API (for Machine B to join)
New-NetFirewallRule -DisplayName "K3s API" -Direction Inbound -Protocol TCP -LocalPort 6443 -Action Allow

# Dashboard access port
New-NetFirewallRule -DisplayName "App Dashboard" -Direction Inbound -Protocol TCP -LocalPort 30000 -Action Allow

# Docker registry
New-NetFirewallRule -DisplayName "Docker Registry" -Direction Inbound -Protocol TCP -LocalPort 5050 -Action Allow

# K3s agent communication
New-NetFirewallRule -DisplayName "K3s Kubelet" -Direction Inbound -Protocol TCP -LocalPort 10250 -Action Allow

# K3s VXLAN (overlay network between machines)
New-NetFirewallRule -DisplayName "K3s VXLAN" -Direction Inbound -Protocol UDP -LocalPort 8472 -Action Allow
```

---

## ✅ STEP 11 — Access From ANYWHERE

Once all pods show `Running`, open a browser on:

| Device | URL |
|--------|-----|
| Machine A | `http://localhost:30000` |
| Machine B | `http://192.168.90.10:30000` |
| Your Mobile | `http://192.168.90.10:30000` |
| Anyone on hotspot | `http://192.168.90.10:30000` |

> Replace `192.168.90.10` with Machine A's actual IP everywhere.

---

## 🩺 Verify It's Really Distributed

```bash
# See which physical machine each pod runs on
kubectl get pods -n distributed-storage -o wide

# Expected — Machine A runs:
# orchestrator, metadata-db, auth-service, node-a

# Expected — Machine B runs:
# node-b, node-c
```

Upload a file → it gets sharded:
- Shard 0 → stored on **Machine A** (node_a)
- Shard 1 → stored on **Machine B** (node_b)
- Parity → stored on **Machine B** (node_c)

---

## 🆘 Troubleshooting

| Problem | Fix |
|---------|-----|
| Machine B can't join | Run firewall cmd on Machine A, check port 6443 |
| `ImagePullBackOff` | Check `registries.yaml` on both machines, restart K3s |
| Pod stuck `Pending` | Check labels: `kubectl describe pod <name> -n distributed-storage` |
| Can't reach :30000 | Run the firewall PowerShell commands in Step 10 |
| WSL2 can't reach LAN | See note below ↓ |

### WSL2 Network Tip (Important!)
WSL2 uses a virtual network by default. To expose K3s ports to your LAN, run in **PowerShell as Admin** on Machine A:

```powershell
# Forward port 30000 from LAN → WSL2
netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport=30000 connectaddress=$(wsl hostname -I) connectport=30000

# Forward port 6443 (for Machine B to join)
netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport=6443 connectaddress=$(wsl hostname -I) connectport=6443

# Forward port 5050 (Docker registry)
netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport=5050 connectaddress=$(wsl hostname -I) connectport=5050
```

> This is the most important step for WSL2 — without it, LAN traffic can't reach your K3s inside WSL2!

---

## 📱 Mobile Access

1. Connect your phone to the **same mobile hotspot**
2. Open browser → `http://192.168.90.10:30000`
3. Done! The app works exactly like a website.

---

## 🧹 Quick Commands Reference

```bash
# All pods status
kubectl get pods -n distributed-storage -o wide

# Logs of a service
kubectl logs -f <pod-name> -n distributed-storage

# Restart a service
kubectl rollout restart deployment/orchestrator -n distributed-storage

# Tear down everything
kubectl delete namespace distributed-storage

# Restart local registry
docker restart local-registry
```
