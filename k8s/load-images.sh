#!/bin/bash
# =============================================================
# load-images.sh
# Run this on EACH laptop inside WSL2 BEFORE applying k8s manifests.
# It builds Docker images and imports them directly into K3s containerd.
# NO registry needed.
#
# On Laptop 1 (runs orchestrator/auth/metadata):
#   bash k8s/load-images.sh laptop1
#
# On Laptop 2 (runs storage nodes only):
#   bash k8s/load-images.sh laptop2
# =============================================================

set -e
ROLE="${1:-laptop1}"

echo "=================================================="
echo "  Building & loading images for role: $ROLE"
echo "  (importing directly into K3s containerd)"
echo "=================================================="

build_and_import() {
  local name=$1
  local context=$2
  echo ""
  echo ">>> Building $name ..."
  docker build -t "${name}:latest" "$context"
  echo ">>> Importing $name into K3s ..."
  docker save "${name}:latest" | sudo k3s ctr images import -
  echo "  ✓ $name loaded into K3s"
}

if [ "$ROLE" == "laptop1" ]; then
  # Laptop 1 needs: orchestrator, auth-service, metadata-db
  build_and_import "orchestrator"  "./orchestrator"
  build_and_import "auth-service"  "./auth_service"
  build_and_import "metadata-db"   "./metadata_db"
  echo ""
  echo "=================================================="
  echo "  Laptop 1 images loaded! Verify with:"
  echo "  sudo k3s ctr images list | grep -E 'orchestrator|auth|metadata'"
  echo "=================================================="

elif [ "$ROLE" == "laptop2" ]; then
  # Laptop 2 only needs: storage-node (shared by node_a, node_b, node_c)
  build_and_import "storage-node"  "./storage_node"
  echo ""
  echo "=================================================="
  echo "  Laptop 2 images loaded! Verify with:"
  echo "  sudo k3s ctr images list | grep storage-node"
  echo "=================================================="

else
  echo "ERROR: Unknown role '$ROLE'. Use 'laptop1' or 'laptop2'"
  exit 1
fi
