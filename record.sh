#!/usr/bin/env bash
# =============================================================
#  record.sh — Contrôle des enregistrements Videolog
# =============================================================
#  Usage :
#    ./record.sh start  [room_name]   Lance un enregistrement
#    ./record.sh stop   <egress_id>   Arrête et uploade dans MinIO
#    ./record.sh list                 Liste les enregistrements actifs
#    ./record.sh status <egress_id>   Statut d'un enregistrement
#    ./record.sh play   <egress_id>   URL de lecture depuis MinIO
# =============================================================

set -euo pipefail

COMPOSE="docker compose"
CTL="$COMPOSE run --rm egress-ctl"
MINIO_URL="http://localhost:9001"
ROOM="${2:-poc-room}"

# Couleurs
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

banner() {
  echo -e "${BOLD}${CYAN}🎥 Videolog Egress Controller${NC}"
  echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

cmd_start() {
  banner
  echo -e "${YELLOW}▶ Démarrage de l'enregistrement pour la room : ${BOLD}${ROOM}${NC}"
  echo ""
  OUTPUT=$($CTL start "$ROOM" 2>&1)
  echo "$OUTPUT"

  # Extraire l'egress_id du JSON retourné
  EGRESS_ID=$(echo "$OUTPUT" | grep -o '"egress_id": *"[^"]*"' | head -1 | cut -d'"' -f4)
  if [ -n "$EGRESS_ID" ]; then
    echo ""
    echo -e "${GREEN}✅ Enregistrement actif !${NC}"
    echo -e "   ${BOLD}egress_id :${NC} $EGRESS_ID"
    echo ""
    echo -e "   Pour arrêter : ${CYAN}./record.sh stop $EGRESS_ID${NC}"

    # Sauvegarder l'egress_id courant
    echo "$EGRESS_ID" > .current_egress_id
  fi
}

cmd_stop() {
  EGRESS_ID="${2:-}"
  if [ -z "$EGRESS_ID" ] && [ -f .current_egress_id ]; then
    EGRESS_ID=$(cat .current_egress_id)
    echo -e "${YELLOW}Utilisation de l'egress_id courant : $EGRESS_ID${NC}"
  fi
  if [ -z "$EGRESS_ID" ]; then
    echo -e "${RED}❌ Fournir un egress_id : ./record.sh stop <egress_id>${NC}"
    exit 1
  fi

  banner
  echo -e "${YELLOW}⏹  Arrêt de l'enregistrement : ${BOLD}${EGRESS_ID}${NC}"
  echo ""
  $CTL stop "$EGRESS_ID"
  echo ""
  echo -e "${GREEN}✅ Enregistrement terminé. Fichier uploadé dans MinIO.${NC}"
  echo -e "   Console MinIO : ${CYAN}${MINIO_URL}${NC}"
  echo -e "   Bucket        : ${CYAN}recordings/${NC}"
  rm -f .current_egress_id
}

cmd_list() {
  banner
  echo -e "${YELLOW}📋 Enregistrements actifs :${NC}"
  echo ""
  $CTL list
}

cmd_status() {
  EGRESS_ID="${2:-}"
  if [ -z "$EGRESS_ID" ] && [ -f .current_egress_id ]; then
    EGRESS_ID=$(cat .current_egress_id)
  fi
  if [ -z "$EGRESS_ID" ]; then
    echo -e "${RED}❌ Fournir un egress_id${NC}"; exit 1
  fi
  banner
  echo -e "${YELLOW}📊 Statut de l'enregistrement : ${BOLD}${EGRESS_ID}${NC}"
  echo ""
  $CTL status "$EGRESS_ID"
}

cmd_play() {
  banner
  echo -e "${YELLOW}📁 Fichiers dans MinIO (bucket recordings) :${NC}"
  echo ""
  docker exec vl-minio mc ls --recursive local/recordings/ 2>/dev/null \
    | awk '{print "   "$NF}' \
    | while read -r f; do
        echo -e "   ${CYAN}http://localhost:9000/recordings/${f}${NC}"
      done
  echo ""
  echo -e "   Console MinIO : ${CYAN}${MINIO_URL}${NC}"
  echo -e "   Login : videolog / videolog123"
}

# ── Dispatcher ────────────────────────────────────────────────
case "${1:-help}" in
  start)  cmd_start  "$@" ;;
  stop)   cmd_stop   "$@" ;;
  list)   cmd_list        ;;
  status) cmd_status "$@" ;;
  play)   cmd_play        ;;
  *)
    banner
    echo -e "Usage :"
    echo -e "  ${CYAN}./record.sh start  [room_name]${NC}   Lance un enregistrement"
    echo -e "  ${CYAN}./record.sh stop   [egress_id]${NC}   Arrête (dernier si omis)"
    echo -e "  ${CYAN}./record.sh list${NC}                 Liste les actifs"
    echo -e "  ${CYAN}./record.sh status [egress_id]${NC}   Statut détaillé"
    echo -e "  ${CYAN}./record.sh play${NC}                 URLs des fichiers MinIO"
    ;;
esac
