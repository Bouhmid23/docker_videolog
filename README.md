# Videolog POC — Guide de démarrage

## Architecture déployée

```
[Mobile Simulator] ──DataTrack──▶ [LiveKit Server] ◀── [Telemetry Agent]
                                                               │
                                                         Redis Streams
                                                               │
                                                        [Mongo Writer]
                                                               │
                                                           MongoDB
[Media Simulator] ──segments──▶ [MinIO]
```

## Prérequis

- Docker >= 24
- Docker Compose >= 2.20
- Ports libres : 3000, 7880, 7881, 7882, 8001, 9000, 9001, 27017, 6379

## Démarrage rapide

```bash
# 1. Cloner / se placer dans le dossier
cd videolog-poc

# 2. Lancer tout le stack
docker compose up --build

# 3. Attendre ~30 secondes que tout démarre
# Suivre les logs :
docker compose logs -f

# 4. Ouvrir les interfaces
```

## Interfaces disponibles

| Service | URL | Credentials |
|---------|-----|-------------|
| **Dashboard** | http://localhost:3000 | — |
| **Dashboard API** | http://localhost:8001/docs | — |
| **LiveKit** | http://localhost:7880 | — |
| **MinIO Console** | http://localhost:9001 | videolog / videolog123 |
| **MongoDB** | mongodb://localhost:27017 | — |
| **Redis** | redis://localhost:6379 | — |

## Vérifier que tout fonctionne

```bash
# Statut des containers
docker compose ps

# Logs par service
docker compose logs telemetry-agent -f
docker compose logs mongo-writer -f
docker compose logs media-simulator -f

# Vérifier Redis Streams
docker exec vl-redis redis-cli XLEN telemetry:stream

# Vérifier MongoDB
docker exec vl-mongo mongosh videolog --eval "db.telemetry.countDocuments()"

# Vérifier MinIO
docker exec vl-minio mc ls local/segments/
```

## Ce que tu vois dans le dashboard

1. **Stats temps réel** : vitesse, RPM, PTS vidéo, nb d'événements MongoDB
2. **Pipeline status** : chaque brique verte = OK
3. **Graphiques** : speed et RPM en série temporelle
4. **Trajectoire GPS** : circuit simulé
5. **Derniers événements** : table live des docs MongoDB

## Commandes utiles

```bash
# Arrêter
docker compose down

# Arrêter + supprimer les volumes (reset complet)
docker compose down -v

# Rebuild un seul service
docker compose up --build telemetry-agent

# Shell dans un container
docker exec -it vl-mongo mongosh videolog
docker exec -it vl-redis redis-cli
```

## Structure du projet

```
videolog-poc/
├── docker-compose.yml          ← Orchestration principale
├── livekit/
│   └── config.yaml             ← Config LiveKit Server
├── telemetry-agent/
│   ├── Dockerfile
│   └── agent.py                ← Écoute DataTracks → Redis Streams
├── mongo-writer/
│   ├── Dockerfile              ← Mongo Writer
│   ├── Dockerfile.api          ← Dashboard API
│   ├── writer.py               ← Redis Streams → MongoDB bulk insert
│   ├── api.py                  ← FastAPI REST pour le dashboard
│   └── init.js                 ← Init MongoDB (index)
├── media-simulator/
│   ├── Dockerfile
│   └── simulator.py            ← Simule mobile + Egress → MinIO
└── web-dashboard/
    ├── Dockerfile
    └── index.html              ← Dashboard temps réel
```

## Prochaines étapes (post-POC)

- [ ] Remplacer `media-simulator` par LiveKit Egress réel
- [ ] Ajouter authentification JWT sur tous les endpoints
- [ ] Déployer TURN server (Coturn) pour NAT traversal
- [ ] Cluster LiveKit (2+ nœuds + Redis partagé)
- [ ] MinIO mode distribué (4 nœuds)
- [ ] Monitoring Prometheus + Grafana

---

## Enregistrement avec LiveKit Egress

### Depuis le dashboard (recommandé)
Ouvrir http://localhost:3000 → panneau **Enregistrement Egress** → bouton **Start**.

### Depuis le CLI (terminal)
```bash
# Lancer un enregistrement
docker compose run --rm --profile tools egress-ctl start poc-room

# Lancer avec layout grille
docker compose run --rm --profile tools egress-ctl start poc-room --layout grid

# Voir les enregistrements actifs
docker compose run --rm --profile tools egress-ctl list

# Arrêter un enregistrement
docker compose run --rm --profile tools egress-ctl stop <egress_id>

# Démarrer + surveiller jusqu'à Ctrl-C (mode watch)
docker compose run --rm --profile tools egress-ctl watch poc-room
```

### Via l'API REST
```bash
# Démarrer
curl -X POST "http://localhost:8001/recordings/start?room_name=poc-room&layout=speaker"

# Lister les actifs
curl http://localhost:8001/recordings

# Arrêter
curl -X POST "http://localhost:8001/recordings/stop?egress_id=<id>"

# Lister les fichiers MinIO
curl http://localhost:8001/recordings/files
```

### Récupérer le fichier MP4
1. MinIO Console → http://localhost:9001 (videolog / videolog123)
2. Bucket `recordings` → naviguer dans `poc-room/<timestamp>/recording.mp4`
3. Ou via l'API : `curl http://localhost:8001/recordings/files`

### Layouts disponibles
| Layout | Description |
|--------|-------------|
| `speaker` | Speaker principal en grand + vignettes |
| `grid` | Grille égale tous participants |
| `single-speaker` | Plein écran 1 participant |

### Notes importantes sur Egress en Docker
- Egress lance **Chromium headless** pour renderer la room WebRTC → besoin de `cap_add: SYS_ADMIN`
- Le fichier MP4 est d'abord écrit dans `/tmp/egress` puis uploadé dans MinIO à la fin
- Sur Mac/Linux : `ipc: host` + `seccomp:unconfined` obligatoires
- Le premier démarrage prend ~30s (téléchargement de Chromium)
