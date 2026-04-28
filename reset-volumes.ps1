# reset-volumes.ps1 — Supprimer les volumes corrompus et redémarrer
# Lance depuis le dossier videolog-poc avec: .\reset-volumes.ps1

Write-Host "Arret des containers..." -ForegroundColor Yellow
docker compose down

Write-Host "Suppression des volumes corrompus..." -ForegroundColor Yellow
docker volume rm videolog-poc_redis_data 2>$null
docker volume rm videolog-poc_minio_data 2>$null

Write-Host "Reconstruction et demarrage..." -ForegroundColor Green
docker compose --profile simulator up --build
