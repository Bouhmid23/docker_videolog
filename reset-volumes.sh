#!/usr/bin/env bash
# reset-volumes.sh — Supprimer les volumes corrompus et redémarrer
set -e
echo "Arret des containers..."
docker compose down

echo "Suppression des volumes corrompus..."
docker volume rm videolog-poc_redis_data 2>/dev/null || true
docker volume rm videolog-poc_minio_data 2>/dev/null || true

echo "Reconstruction et demarrage..."
docker compose --profile simulator up --build
