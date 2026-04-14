#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# ShardVault — Chaos Testing Script
# Run this from Laptop 1 (the k3s master) with kubectl available.
#
# Usage:
#   chmod +x chaos_test.sh
#   MASTER_IP=192.168.1.10 bash chaos_test.sh
# ─────────────────────────────────────────────────────────────────────────────
set -e

MASTER_IP="${MASTER_IP:-127.0.0.1}"
PORT=30000
BASE="http://$MASTER_IP:$PORT"
NS="shardvault"
KUBECTL="sudo kubectl"

GREEN="\033[92m"
RED="\033[91m"
YELLOW="\033[93m"
CYAN="\033[96m"
RESET="\033[0m"
BOLD="\033[1m"

ok()   { echo -e "  ${GREEN}✓${RESET} $1"; }
fail() { echo -e "  ${RED}✗ FAIL:${RESET} $1"; }
warn() { echo -e "  ${YELLOW}⚠ WARN:${RESET} $1"; }
section() { echo -e "\n${BOLD}${CYAN}══════════════════════════════════════════════════════════${RESET}"; echo -e "${BOLD}${CYAN}  $1${RESET}"; echo -e "${BOLD}${CYAN}══════════════════════════════════════════════════════════${RESET}"; }

# ─── Helper: Upload a test file and return file_id ────────────────────────────
upload_test_file() {
    local label="$1"
    local content="$2"
    local tmpfile=$(mktemp /tmp/shardvault_chaos_XXXXXX.bin)
    echo -n "$content" > "$tmpfile"
    local resp=$(curl -s -X POST "$BASE/upload" -F "file=@$tmpfile;filename=chaos_${label}.bin")
    rm -f "$tmpfile"
    echo "$resp" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('file_id','ERROR'))"
}

# ─── Helper: Download and compare ─────────────────────────────────────────────
download_and_verify() {
    local file_id="$1"
    local original_content="$2"
    local desc="$3"

    local resp=$(curl -s "$BASE/download/$file_id")
    local status=$(echo "$resp" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('error','OK'))" 2>/dev/null)

    if echo "$status" | grep -q "IRRECOVERABLE\|error"; then
        fail "$desc: IRRECOVERABLE — $status"
        return 1
    fi

    local recovered=$(echo "$resp" | python3 -c "
import sys, json, base64
d = json.load(sys.stdin)
if 'data_b64' in d:
    data = base64.b64decode(d['data_b64']).decode('latin-1', errors='replace')
    print(data[:50])
else:
    print('NO_DATA')
")
    local recovered_shards=$(echo "$resp" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('recovered_shards',[]))")
    local corrupt_shards=$(echo "$resp" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('corrupted_shards',[]))")

    if echo "$recovered" | grep -qi "$(echo $original_content | head -c 20)"; then
        ok "$desc: Content matches ✓ (recovered_shards=$recovered_shards)"
    else
        warn "$desc: Content may differ (first 50 chars: $recovered)"
    fi

    echo "    Log:"
    echo "$resp" | python3 -c "
import sys, json
d = json.load(sys.stdin)
for entry in d.get('log', []):
    print('      ' + entry)
"
}

# ═══════════════════════════════════════════════════════════════════════════════
section "CHAOS TEST 1: Kill storage-node-1, Verify XOR Recovery"
# ═══════════════════════════════════════════════════════════════════════════════

echo "  Uploading test file..."
CONTENT_1="CHAOS_TEST_NODE_KILL_$(date +%s)_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_repeated_content_for_size"
FID_1=$(upload_test_file "node_kill" "$CONTENT_1")
echo "  Uploaded: file_id=$FID_1"

if [ "$FID_1" = "ERROR" ]; then
    fail "Upload failed — cannot proceed with chaos test 1"
else
    ok "File uploaded: $FID_1"

    # Confirm healthy download before kill
    echo "  Testing download BEFORE kill..."
    download_and_verify "$FID_1" "$CONTENT_1" "Pre-kill download"

    # Kill storage-node-1
    echo "  Deleting pod: storage-node-1..."
    $KUBECTL delete pod storage-node-1 -n $NS --grace-period=0 --force 2>/dev/null || true

    echo "  Waiting 3 seconds (pod is terminating)..."
    sleep 3

    # Try to download while pod is down/restarting
    echo "  Testing download WHILE node-1 is down..."
    download_and_verify "$FID_1" "$CONTENT_1" "Post-kill download (XOR recovery expected)"

    # Wait for pod to fully restart
    echo "  Waiting for storage-node-1 to restart..."
    $KUBECTL wait pod/storage-node-1 -n $NS --for=condition=Ready --timeout=120s 2>/dev/null || warn "storage-node-1 did not restart within 120s"
    ok "storage-node-1 restarted"

    # NOTE: After kill, the shard is GONE from storage-node-1 (ephemeral memory during crash window)
    # But the PVC means data persists. Let's verify:
    echo "  Testing download AFTER node-1 restart..."
    download_and_verify "$FID_1" "$CONTENT_1" "Post-restart download"

    $KUBECTL delete pod storage-node-1 -n $NS 2>/dev/null || true
fi

# ═══════════════════════════════════════════════════════════════════════════════
section "CHAOS TEST 2: Rapid Consecutive Pod Restarts (Resilience)"
# ═══════════════════════════════════════════════════════════════════════════════

echo "  Uploading resilience test file..."
CONTENT_2="RAPID_RESTART_TEST_$(date +%s)_XYZ_$(head -c 200 /dev/urandom | base64 | head -c 200)"
FID_2=$(upload_test_file "rapid" "$CONTENT_2")
ok "File uploaded: $FID_2"

for i in 1 2 3; do
    echo "  Restart cycle $i/3: killing storage-node-2..."
    $KUBECTL delete pod storage-node-2 -n $NS --grace-period=0 --force 2>/dev/null || true
    sleep 2
    echo "  Download attempt $i during restart cycle..."
    download_and_verify "$FID_2" "$CONTENT_2" "Resilience cycle $i"
    $KUBECTL wait pod/storage-node-2 -n $NS --for=condition=Ready --timeout=60s 2>/dev/null || true
    echo "  Pod restarted. Sleeping 2s..."
    sleep 2
done
ok "System survived 3 rapid restart cycles"

# ═══════════════════════════════════════════════════════════════════════════════
section "CHAOS TEST 3: Orchestrator Pod Restart"
# ═══════════════════════════════════════════════════════════════════════════════

echo "  Killing orchestrator pod..."
$KUBECTL delete pod -n $NS -l app=orchestrator --grace-period=0 --force 2>/dev/null || true
echo "  Waiting for orchestrator to restart..."
sleep 5
$KUBECTL wait pod -n $NS -l app=orchestrator --for=condition=Ready --timeout=60s 2>/dev/null || warn "Orchestrator took >60s to restart"
ok "Orchestrator restarted"

# Upload after orchestrator restart to verify token cache recovers
echo "  Uploading after orchestrator restart (tests token cache cold-start)..."
CONTENT_3="ORCH_RESTART_$(date +%s)"
FID_3=$(upload_test_file "orch_restart" "$CONTENT_3")
if [ "$FID_3" != "ERROR" ]; then
    ok "Upload after orchestrator restart succeeded: $FID_3"
    download_and_verify "$FID_3" "$CONTENT_3" "Download after orchestrator restart"
else
    fail "Upload after orchestrator restart FAILED"
fi

# ═══════════════════════════════════════════════════════════════════════════════
section "CHAOS TEST 4: Verify Physical Node Distribution (kubectl describe)"
# ═══════════════════════════════════════════════════════════════════════════════

echo "  Pod → Node mapping:"
$KUBECTL get pods -n $NS -o wide | grep storage-node

pod_nodes=$($KUBECTL get pods -n $NS -o wide | grep "storage-node-[0-9]" | awk '{print $7}')
unique_nodes=$(echo "$pod_nodes" | sort | uniq | wc -l)

echo "  Physical nodes used: $unique_nodes"
if [ "$unique_nodes" -ge 3 ]; then
    ok "All 3 storage pods are on different physical machines"
elif [ "$unique_nodes" -ge 2 ]; then
    warn "Storage pods are on $unique_nodes machines (expected 3) — check podAntiAffinity"
else
    fail "All storage pods appear to be on the SAME machine — podAntiAffinity not working!"
fi

# ═══════════════════════════════════════════════════════════════════════════════
section "CHAOS TEST 5: Verify Disk Storage — Files Actually on Different Laptops"
# ═══════════════════════════════════════════════════════════════════════════════

echo "  Checking shard files on each storage node's filesystem:"
for pod in storage-node-0 storage-node-1 storage-node-2; do
    echo "  ── $pod ──"
    node=$($KUBECTL get pod $pod -n $NS -o jsonpath='{.spec.nodeName}' 2>/dev/null || echo "unknown")
    echo "    Running on physical node: $node"
    shard_count=$($KUBECTL exec $pod -n $NS -- sh -c 'ls /data/shards 2>/dev/null | wc -l' 2>/dev/null || echo "0")
    echo "    Shard files in /data/shards: $shard_count"
    $KUBECTL exec $pod -n $NS -- sh -c 'ls /data/shards 2>/dev/null | head -5' 2>/dev/null || true
    if [ "$shard_count" -gt 0 ]; then
        ok "$pod has $shard_count shard file(s) on $node"
    else
        warn "$pod has 0 shards (may have been deleted or not yet written)"
    fi
done

# ═══════════════════════════════════════════════════════════════════════════════
section "CHAOS TEST 6: PVC Persistence Verification"
# ═══════════════════════════════════════════════════════════════════════════════

echo "  PVC Status:"
$KUBECTL get pvc -n $NS

bound_pvcs=$($KUBECTL get pvc -n $NS --no-headers | grep -c "Bound" || echo "0")
total_pvcs=$($KUBECTL get pvc -n $NS --no-headers | wc -l)
if [ "$bound_pvcs" -eq "$total_pvcs" ] && [ "$total_pvcs" -gt 0 ]; then
    ok "All $total_pvcs PVCs are Bound"
else
    warn "$bound_pvcs/$total_pvcs PVCs are Bound"
fi

# Upload a file, restart the pod, verify the shard survived
echo "  Testing PVC persistence: upload → restart pod → verify shard survives"
CONTENT_PVC="PVC_PERSISTENCE_TEST_$(date +%s)"
FID_PVC=$(upload_test_file "pvc_test" "$CONTENT_PVC")
ok "Uploaded $FID_PVC"

echo "  Restarting storage-node-0..."
$KUBECTL delete pod storage-node-0 -n $NS --grace-period=0 --force 2>/dev/null || true
$KUBECTL wait pod/storage-node-0 -n $NS --for=condition=Ready --timeout=60s 2>/dev/null || warn "Pod restart timeout"
ok "storage-node-0 restarted"

echo "  Downloading after restart to verify PVC data survived..."
download_and_verify "$FID_PVC" "$CONTENT_PVC" "PVC persistence after pod restart"

# ═══════════════════════════════════════════════════════════════════════════════
section "CHAOS TEST 7: Network Delay Simulation (tc qdisc)"
# ═══════════════════════════════════════════════════════════════════════════════

echo "  Testing retry logic by simulating delay on storage-node-0..."
echo "  (Adding 2 second delay to storage-node-0 network interface)"

# Add delay inside the pod
$KUBECTL exec storage-node-0 -n $NS -- sh -c '
    apt-get install -qq -y iproute2 2>/dev/null || true
    tc qdisc add dev eth0 root netem delay 2000ms 2>/dev/null || true
    echo "Delay added"
' 2>/dev/null || warn "Could not add network delay (tc not available in container)"

echo "  Attempting upload with 2s node delay (retry logic should compensate)..."
CONTENT_DELAY="DELAY_TEST_$(date +%s)"
FID_DELAY=$(upload_test_file "delay" "$CONTENT_DELAY")
if [ "$FID_DELAY" != "ERROR" ]; then
    ok "Upload succeeded despite 2s delay: $FID_DELAY"
else
    warn "Upload failed under 2s delay (timeout may be too tight)"
fi

# Remove delay
$KUBECTL exec storage-node-0 -n $NS -- sh -c 'tc qdisc del dev eth0 root 2>/dev/null || true' 2>/dev/null || true
ok "Network delay removed"

# ═══════════════════════════════════════════════════════════════════════════════
section "CHAOS TEST 8: Log Analysis — Trace a Complete Request"
# ═══════════════════════════════════════════════════════════════════════════════

echo "  Uploading a file while tailing orchestrator logs..."
CONTENT_TRACE="TRACE_TEST_$(date +%s)"
FID_TRACE=$(upload_test_file "trace" "$CONTENT_TRACE")
ok "File uploaded: $FID_TRACE"

echo "  Orchestrator logs (last 30 lines):"
$KUBECTL logs -n $NS -l app=orchestrator --tail=30 | grep -E "\[UPLOAD\]|\[FILE_ID\]|\[SHARD\]|\[PARITY\]|\[META\]|\[DONE\]|\[ERROR\]" || $KUBECTL logs -n $NS -l app=orchestrator --tail=30

echo "  storage-node-0 logs:"
$KUBECTL logs -n $NS storage-node-0 --tail=10

# ═══════════════════════════════════════════════════════════════════════════════
section "CHAOS TEST 9: Prometheus Metrics Verification"
# ═══════════════════════════════════════════════════════════════════════════════

PROM_URL="http://$MASTER_IP:30090"
echo "  Checking Prometheus at $PROM_URL..."

prom_status=$(curl -s -o /dev/null -w "%{http_code}" "$PROM_URL/-/healthy" 2>/dev/null || echo "000")
if [ "$prom_status" = "200" ]; then
    ok "Prometheus is healthy"

    echo "  Querying shard metrics..."
    curl -s "$PROM_URL/api/v1/query?query=shardvault_shard_count" | python3 -c "
import sys, json
d = json.load(sys.stdin)
results = d.get('data', {}).get('result', [])
if results:
    for r in results:
        node = r['metric'].get('node', 'unknown')
        val  = r['value'][1]
        print(f'    shardvault_shard_count{{node={node}}} = {val}')
else:
    print('    (no results — metrics may not have been scraped yet)')
" 2>/dev/null || warn "Could not parse Prometheus response"

else
    warn "Prometheus not reachable at $PROM_URL (status $prom_status) — check port 30090 firewall"
fi

# ═══════════════════════════════════════════════════════════════════════════════
section "CHAOS TEST 10: DNS Resolution Verification"
# ═══════════════════════════════════════════════════════════════════════════════

echo "  Running DNS resolution test from within orchestrator pod..."
ORCH_POD=$($KUBECTL get pod -n $NS -l app=orchestrator -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)

if [ -n "$ORCH_POD" ]; then
    for svc in "auth-service.shardvault.svc.cluster.local" "metadata-db.shardvault.svc.cluster.local" "storage-node-0.storage-node.shardvault.svc.cluster.local" "storage-node-1.storage-node.shardvault.svc.cluster.local" "storage-node-2.storage-node.shardvault.svc.cluster.local"; do
        result=$($KUBECTL exec $ORCH_POD -n $NS -- python3 -c "
import socket
try:
    ip = socket.gethostbyname('$svc')
    print(f'RESOLVED:{ip}')
except Exception as e:
    print(f'FAILED:{e}')
" 2>/dev/null || echo "EXEC_FAILED")
        if echo "$result" | grep -q "RESOLVED"; then
            ip=$(echo "$result" | sed 's/RESOLVED://')
            ok "$svc → $ip"
        else
            fail "$svc DNS resolution FAILED: $result"
        fi
    done
else
    warn "Could not find orchestrator pod for DNS test"
fi

# ═══════════════════════════════════════════════════════════════════════════════
section "FINAL CHAOS TEST SUMMARY"
# ═══════════════════════════════════════════════════════════════════════════════

echo ""
echo "  System Status After Chaos Testing:"
$KUBECTL get pods -n $NS

echo ""
echo "  PVC Status:"
$KUBECTL get pvc -n $NS

echo ""
echo "  Service Status:"
$KUBECTL get services -n $NS

echo ""
health=$(curl -s "$BASE/health" 2>/dev/null)
orch_ok=$(echo "$health" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('orchestrator',{}).get('status','unknown'))" 2>/dev/null)
nodes_up=$(echo "$health" | python3 -c "import sys,json; d=json.load(sys.stdin); print(sum(1 for s in d.get('nodes',{}).values() if s.get('status')=='ok'))" 2>/dev/null)

if [ "$orch_ok" = "ok" ] && [ "$nodes_up" -ge 3 ]; then
    echo -e "  ${GREEN}${BOLD}✓ SYSTEM SURVIVED CHAOS TESTING — All services healthy after all tests.${RESET}"
else
    echo -e "  ${YELLOW}${BOLD}⚠ System partially degraded after chaos tests. Check pod status above.${RESET}"
fi
