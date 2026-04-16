#!/bin/bash
# =============================================================
# build-and-push.sh
# Run this ONCE on Laptop 1 (the master) from the project root.
# It builds all Docker images and pushes them to the local registry.
#
# Usage:
#   chmod +x k8s/build-and-push.sh
#   ./k8s/build-and-push.sh <LAPTOP1_IP>
#
# Example:
#   ./k8s/build-and-push.sh 192.168.1.100
# =============================================================

set -e   # Exit immediately on any error

# ── Arguments ────────────────────────────────────────────────
LAPTOP1_IP="${1:-}"
if [ -z "$LAPTOP1_IP" ]; then
  echo "ERROR: Please provide Laptop 1's IP address."
  echo "Usage: ./k8s/build-and-push.sh <LAPTOP1_IP>"
  exit 1
fi

REGISTRY="${LAPTOP1_IP}:5050"
echo "==================================================="
echo "  Registry: ${REGISTRY}"
echo "==================================================="

# ── Step 1: Start local registry (if not already running) ────
echo ""
echo "[1/7] Starting local Docker registry on port 5050..."
if [ "$(docker ps -q -f name=local-registry)" ]; then
  echo "  → Registry already running, skipping."
else
  docker run -d \
    --name local-registry \
    --restart=always \
    -p 5050:5000 \
    registry:2
  echo "  → Registry started."
fi

# ── Step 2: Build orchestrator ────────────────────────────────
echo ""
echo "[2/7] Building orchestrator..."
docker build -t "${REGISTRY}/orchestrator:latest" ./orchestrator
docker push "${REGISTRY}/orchestrator:latest"
echo "  ✓ orchestrator pushed"

# ── Step 3: Build auth_service ────────────────────────────────
echo ""
echo "[3/7] Building auth-service..."
docker build -t "${REGISTRY}/auth-service:latest" ./auth_service
docker push "${REGISTRY}/auth-service:latest"
echo "  ✓ auth-service pushed"

# ── Step 4: Build metadata_db ─────────────────────────────────
echo ""
echo "[4/7] Building metadata-db..."
docker build -t "${REGISTRY}/metadata-db:latest" ./metadata_db
docker push "${REGISTRY}/metadata-db:latest"
echo "  ✓ metadata-db pushed"

# ── Step 5: Build storage_node (used by node_a, node_b, node_c)
echo ""
echo "[5/7] Building storage-node (shared image for A/B/C)..."
docker build -t "${REGISTRY}/storage-node:latest" ./storage_node
docker push "${REGISTRY}/storage-node:latest"
echo "  ✓ storage-node pushed"

# ── Step 6: Patch REGISTRY_IP placeholder in all manifests ────
echo ""
echo "[6/7] Patching REGISTRY_IP in all K8s manifests..."
for f in k8s/*.yaml; do
  sed -i "s|REGISTRY_IP|${LAPTOP1_IP}|g" "$f"
  echo "  → Patched ${f}"
done

# ── Step 7: Summary ───────────────────────────────────────────
echo ""
echo "==================================================="
echo " ALL IMAGES BUILT & PUSHED SUCCESSFULLY!"
echo "==================================================="
echo ""
echo " Registry URL : http://${REGISTRY}"
echo ""
echo " Next: Run the K3s setup (see k8s/SETUP.md), then:"
echo "   kubectl apply -f k8s/"
echo ""
echo " To list pushed images:"
echo "   curl http://${REGISTRY}/v2/_catalog"
echo "==================================================="
