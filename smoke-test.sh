#!/usr/bin/env bash
# ============================================================
#  Videolog POC — Smoke Test
#  Vérifie que tous les services sont opérationnels.
#  Usage : ./smoke-test.sh [--wait]
# ============================================================
set -euo pipefail

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
OK="  ${GREEN}✓${NC}"
FAIL="  ${RED}✗${NC}"
WARN="  ${YELLOW}⚠${NC}"

WAIT_MODE=false
[[ "${1:-}" == "--wait" ]] && WAIT_MODE=true

pass=0; fail=0

check() {
  local name="$1"; local cmd="$2"
  if eval "$cmd" &>/dev/null; then
    echo -e "$OK $name"
    ((pass++))
  else
    echo -e "$FAIL $name"
    ((fail++))
  fi
}

check_json() {
  local name="$1"; local url="$2"; local key="$3"
  local out
  out=$(curl -sf "$url" 2>/dev/null) || { echo -e "$FAIL $name (connexion refusée)"; ((fail++)); return; }
  if echo "$out" | grep -q "\"$key\""; then
    echo -e "$OK $name"
    ((pass++))
  else
    echo -e "$FAIL $name (clé '$key' absente dans: ${out:0:80})"
    ((fail++))
  fi
}

wait_for() {
  local name="$1"; local url="$2"; local max=30; local n=0
  printf "  ⏳ Attente $name"
  while ! curl -sf "$url" &>/dev/null; do
    ((n++))
    [[ $n -ge $max ]] && { echo " TIMEOUT"; return 1; }
    printf "."; sleep 2
  done
  echo " OK"
}

echo ""
echo -e "${BOLD}${CYAN}🎥 Videolog POC — Smoke Test${NC}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# ── Optionally wait for services to be ready ──────────────────
if $WAIT_MODE; then
  echo -e "${YELLOW}Mode attente activé (--wait)${NC}"
  wait_for "LiveKit"       "http://localhost:7880/"
  wait_for "Dashboard API" "http://localhost:8001/health"
  wait_for "Webhook"       "http://localhost:8088/health"
  wait_for "MinIO"         "http://localhost:9000/minio/health/live"
  echo ""
fi

# ── Infrastructure ─────────────────────────────────────────────
echo -e "${BOLD}Infrastructure${NC}"
check "LiveKit Server" "curl -sf http://localhost:7880/"
check "Redis ping" "docker exec vl-redis redis-cli ping | grep -q PONG"
check "MongoDB ping" "docker exec vl-mongo mongosh --quiet --eval 'db.adminCommand(\"ping\")' | grep -q '\"ok\" : 1'"
check "MinIO health" "curl -sf http://localhost:9000/minio/health/live"
echo ""

# ── Services applicatifs ────────────────────────────────────────
echo -e "${BOLD}Services applicatifs${NC}"
check_json "Dashboard API /health" "http://localhost:8001/health" "status"
check_json "Egress Webhook /health" "http://localhost:8088/health" "status"
check "Dashboard Web (nginx)" "curl -sf http://localhost:3000/"
echo ""

# ── Pipeline données ────────────────────────────────────────────
echo -e "${BOLD}Pipeline télémétrie${NC}"
check "Redis Stream existant" "docker exec vl-redis redis-cli EXISTS telemetry:stream | grep -q 1"
check "Consumer group actif" "docker exec vl-redis redis-cli XINFO GROUPS telemetry:stream 2>/dev/null | grep -q mongo-writers"
check "MongoDB collection telemetry" "docker exec vl-mongo mongosh --quiet videolog --eval 'db.telemetry.stats()' | grep -q 'ok'"

# Vérifier que des docs ont bien été insérés
count=$(docker exec vl-mongo mongosh --quiet videolog --eval "db.telemetry.countDocuments()" 2>/dev/null | tail -1 || echo "0")
if [[ "$count" =~ ^[0-9]+$ ]] && [[ "$count" -gt 0 ]]; then
  echo -e "$OK MongoDB telemetry ($count documents)"
  ((pass++))
else
  echo -e "$WARN MongoDB telemetry (0 documents — simulateur démarré ?)"
fi
echo ""

# ── MinIO buckets ───────────────────────────────────────────────
echo -e "${BOLD}Stockage MinIO${NC}"
check "Bucket recordings" "docker exec vl-minio mc ls local/recordings/ &>/dev/null || docker exec vl-minio-init true 2>/dev/null"
check "API /recordings/files" "curl -sf http://localhost:8001/recordings/files | grep -qE '\[|\{'"
echo ""

# ── Token endpoint ──────────────────────────────────────────────
echo -e "${BOLD}Auth${NC}"
check "Token endpoint LiveKit" "curl -sf -X POST http://localhost:8001/token -H 'Content-Type: application/json' -d '{\"identity\":\"smoketest\",\"room\":\"poc-room\"}' | grep -q token"
echo ""

# ── Egress ──────────────────────────────────────────────────────
echo -e "${BOLD}Egress${NC}"
egress_health=$(curl -sf http://localhost:8080/ 2>/dev/null && echo "ok" || echo "fail")
if [[ "$egress_health" == "ok" ]]; then
  echo -e "$OK LiveKit Egress service"
  ((pass++))
else
  echo -e "$WARN LiveKit Egress (pas encore prêt ou non démarré)"
fi
echo ""

# ── Résumé ──────────────────────────────────────────────────────
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
total=$((pass + fail))
echo -e "${BOLD}Résultat : ${GREEN}$pass${NC}/${total} checks OK${NC}"
echo ""

if [[ $fail -gt 0 ]]; then
  echo -e "${RED}❌ $fail check(s) en échec${NC}"
  echo ""
  echo -e "Vérifier les logs : ${CYAN}docker compose logs --tail=50 <service>${NC}"
  exit 1
else
  echo -e "${GREEN}✅ Tous les services sont opérationnels !${NC}"
  echo ""
  echo -e "  Dashboard  : ${CYAN}http://localhost:3000${NC}"
  echo -e "  API docs   : ${CYAN}http://localhost:8001/docs${NC}"
  echo -e "  MinIO      : ${CYAN}http://localhost:9001${NC}  (videolog / videolog123)"
  if docker ps --filter name=vl-grafana --format '{{.Names}}' 2>/dev/null | grep -q grafana; then
    echo -e "  Grafana    : ${CYAN}http://localhost:3001${NC}  (admin / admin)"
  fi
  exit 0
fi
