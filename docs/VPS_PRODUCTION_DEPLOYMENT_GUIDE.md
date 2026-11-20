# Guide Complet de Déploiement VPS & Optimisation Performance — Videolog

Ce guide couvre de bout en bout la mise en production de la stack **Videolog** sur un VPS Linux (**Ubuntu 24.04 LTS** ou **Debian 12**) ainsi que tous les réglages système, réseau, Docker, LiveKit, MongoDB, Redis, FastAPI et Caddy nécessaires pour obtenir des performances optimales et une fiabilité maximale.

> **Base technique couverte** : Caddy 2.8 (HTTPS/HTTP3), LiveKit v1.7.2 + Egress v1.8.4, FastAPI/Uvicorn (`dashboard-api`), Arq Worker, Telemetry Agent, Mongo Writer, MongoDB 7.0, Redis 7.2, MinIO S3 et Back-office React/Nginx (`backoffice`).

---

## 1. Architecture de production & Dimensionnement VPS

### 1.1 Topologie réseau

```text
Internet (HTTPS / WSS / WebRTC)
 ├── 80/tcp, 443/tcp, 443/udp (HTTP/3) ──▶ [vl-caddy] (TLS Let's Encrypt auto)
 │      ├── app.<DOMAIN>       ──▶ vl-backoffice:80  +  /api/* ──▶ vl-dashboard-api:8000
 │      ├── api.<DOMAIN>       ──▶ vl-dashboard-api:8000 (/token, /mobile/*, /v1/*, /webhook/livekit)
 │      ├── livekit.<DOMAIN>   ──▶ vl-livekit:7880 (signalisation WSS)
 │      └── minio.<DOMAIN>     ──▶ vl-minio:9000   (liens vidéo MP4 signés)
 ├── 7881/tcp                  ──▶ [vl-livekit] (WebRTC ICE/TCP fallback)
 ├── 7882/udp                  ──▶ [vl-livekit] (WebRTC média UDP mux)
 └── 3478/udp, 5349/tcp (opt.) ──▶ [vl-livekit] (TURN intégré pour 4G/5G restrictives)

Réseau Docker privé `videolog` (aucun port public sur 0.0.0.0) :
 ├── vl-mongo (MongoDB 7.0, auth obligatoire)
 ├── vl-redis (Redis 7.2, Streams + PubSub + Arq + Rate-limit)
 ├── vl-egress (Chromium headless + FFmpeg)
 ├── vl-egress-webhook (webhooks LiveKit + garde-fou durée max)
 ├── vl-telemetry-agent (LiveKit DataTracks → Redis Streams)
 ├── vl-mongo-writer (Redis Streams → MongoDB batch)
 └── vl-worker (Arq : finalisation live, calcul activités & PMC)
```

### 1.2 Dimensionnement matériel recommandé

Le premier consommateur CPU/RAM de la plateforme est **`vl-egress`** (Chromium headless + encodage H.264), qui consomme **~2 à 4 vCPU** et **~1.5 à 2.5 Go RAM** par enregistrement simultané.

| Profil | Charge cible | CPU | RAM | Disque | Bande passante | Exemples fournisseurs |
|---|---|---|---|---|---|---|
| **Starter (MVP / 1–3 clubs)** | 1–2 rooms live, 1 enregistrement simultané, < 50 athlètes | **4 vCPU** | **8 Go** | **80–160 Go NVMe** | 1 Gbps | Hetzner CPX31 / CCX13, OVH Comfort, Scaleway DEV1-L |
| **Production (recommandé)** | 3–6 rooms live, 2–3 enregistrements simultanés, 50–300 athlètes | **8 vCPU dédiés** | **16 Go** | **240+ Go NVMe** | 1 Gbps+ | Hetzner CCX23 / AX41-NVMe, OVH Elite, Scaleway GP1-S |
| **Scale (Multi-clubs)** | 6+ rooms, > 3 enregistrements simultanés | **16 vCPU** (ou 2e nœud `egress`) | **32 Go** | **NVMe + S3 managé** | 1–10 Gbps | Déporter `egress` sur un 2e VPS + stockage Cloudflare R2 / Scaleway S3 |

> **Exigences critiques :**
> - **NVMe SSD obligatoire** (pas de HDD) pour MongoDB WiredTiger, les écritures temporaires MP4 de `/tmp/egress` et le streaming MinIO.
> - **Architecture x86_64 (amd64)** recommandée : l'image `livekit/egress:v1.8.4` (Chromium) est bien plus stable et rapide en amd64 qu'en ARM64.
> - **Localisation géographique** : choisissez un datacenter proche de vos athlètes (ex. France/Allemagne : Paris, Gravelines, Francfort, Falkenstein) pour garder une latence WebRTC < 35 ms.

---

## 2. Prérequis DNS & Firebase

### 2.1 Enregistrements DNS

Créez ces enregistrements **`A`** pointant tous vers l'IPv4 publique du VPS (TTL `300` secondes lors de la mise en service) :

| Sous-domaine | Type | Cible | Usage |
|---|---|---|---|
| `@` (`videolog.app`) | `A` | `<IP_VPS>` | Redirection vers `app.<DOMAIN>` |
| `app` (`app.videolog.app`) | `A` | `<IP_VPS>` | Back-office Web React + Live Room |
| `api` (`api.videolog.app`) | `A` | `<IP_VPS>` | API mobile Flutter |
| `livekit` (`livekit.videolog.app`) | `A` | `<IP_VPS>` | Signalisation WebSocket LiveKit (`wss://`) |
| `minio` (`minio.videolog.app`) | `A` | `<IP_VPS>` | Lecture directe des vidéos MP4 via URL signées |
| `turn` (`turn.videolog.app`) | `A` | `<IP_VPS>` | Serveur TURN/TLS intégré LiveKit *(fortement recommandé)* |
| `dashboard` (`dashboard.videolog.app`) | `A` | `<IP_VPS>` | *(Optionnel)* Ancien dashboard opérateur si profil `legacy` |

> **Attention Cloudflare :** si vous utilisez Cloudflare comme DNS, laissez **tous ces sous-domaines en mode « DNS Only » (nuage gris)**, en particulier `livekit`, `turn`, `minio` et `api`. Le proxy Cloudflare bloque WebRTC UDP, perturbe les certificats Caddy et bride les gros uploads/streams MP4.

### 2.2 Configuration Firebase Console

1. **Firebase Console → Authentication → Settings → Authorized domains** :
   - Ajoutez impérativement `app.<DOMAIN>` (ex. `app.videolog.app`), sinon la connexion Google Sign-In échouera avec `auth/unauthorized-domain`.
2. **Firebase Console → Project Settings → Service Accounts** :
   - Générez la clé privée JSON (`firebase-service-account.json`) pour le backend (`dashboard-api`).
3. **Firebase Console → Project Settings → Your apps (Web)** :
   - Récupérez `VITE_FIREBASE_API_KEY`, `VITE_FIREBASE_AUTH_DOMAIN`, `VITE_FIREBASE_PROJECT_ID` et `VITE_FIREBASE_APP_ID`.

---

## 3. Préparation, Sécurisation & Tuning OS du VPS

Connectez-vous initialement en `root` sur un VPS **Ubuntu 24.04 LTS** propre.

### 3.1 Mise à jour, utilisateur `deploy` et durcissement SSH

```bash
apt update && apt upgrade -y
apt install -y ca-certificates curl gnupg git ufw fail2ban chrony htop jq unzip rclone

# Créer un utilisateur dédié
adduser --disabled-password --gecos "" deploy
usermod -aG sudo deploy
echo "deploy ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/deploy

# Copier votre clé SSH publique vers deploy
mkdir -p /home/deploy/.ssh
cp /root/.ssh/authorized_keys /home/deploy/.ssh/
chown -R deploy:deploy /home/deploy/.ssh
chmod 700 /home/deploy/.ssh && chmod 600 /home/deploy/.ssh/authorized_keys
```

Durcissez SSH (`/etc/ssh/sshd_config.d/99-hardening.conf`) **après avoir vérifié que la connexion en `deploy` fonctionne dans un 2e terminal** :

```bash
cat <<'EOF' | tee /etc/ssh/sshd_config.d/99-hardening.conf
PermitRootLogin no
PasswordAuthentication no
PubkeyAuthentication yes
X11Forwarding no
MaxAuthTries 3
EOF

sshd -t && systemctl reload ssh
systemctl enable --now fail2ban
```

### 3.2 Synchronisation d'horloge NTP (Critique)

La synchronisation vidéo/télémétrie dans le lecteur Videolog repose sur l'alignement de l'horloge du VPS avec celle des mobiles, et Firebase rejette les tokens au-delà de 10 s de dérive (`clock_skew_seconds=10`).

```bash
systemctl enable --now chrony
chronyc tracking
```

### 3.3 Swap de sécurité (4 Go)

Protège le serveur contre un OOM brutal pendant la première compilation Go de MinIO ou un pic mémoire d'Egress :

```bash
fallocate -l 4G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
echo '/swapfile none swap sw 0 0' | tee -a /etc/fstab
```

### 3.4 Désactiver les Transparent Huge Pages (MongoDB 7 & Redis 7)

MongoDB et Redis déconseillent tous deux THP, qui provoque des pics de latence et une surconsommation RAM :

```bash
cat <<'EOF' | tee /etc/systemd/system/disable-thp.service
[Unit]
Description=Disable Transparent Huge Pages (THP) for MongoDB & Redis
DefaultDependencies=no
After=sysinit.target local-fs.target
Before=docker.service

[Service]
Type=oneshot
ExecStart=/bin/sh -c 'echo never > /sys/kernel/mm/transparent_hugepage/enabled && echo never > /sys/kernel/mm/transparent_hugepage/defrag'

[Install]
WantedBy=basic.target
EOF

systemctl daemon-reload
systemctl enable --now disable-thp.service
cat /sys/kernel/mm/transparent_hugepage/enabled
# Attendu : always madvise [never]
```

### 3.5 Optimisation Kernel (`sysctl`) pour WebRTC, BBR & Redis

Ce fichier est **le réglage le plus impactant côté OS** :
- **Buffers UDP 25 Mo** : évite la perte de paquets WebRTC sur LiveKit sous rafale vidéo/télémétrie.
- **TCP BBR** : améliore nettement le streaming MP4 (MinIO) et la latence API sur 4G/5G avec perte de paquets.
- **`vm.overcommit_memory = 1`** : empêche les échecs de réécriture AOF Redis.

```bash
cat <<'EOF' | tee /etc/sysctl.d/99-videolog-perf.conf
# --- WebRTC / LiveKit UDP buffers ---
net.core.rmem_max = 26214400
net.core.wmem_max = 26214400
net.core.rmem_default = 7340032
net.core.wmem_default = 7340032
net.core.netdev_max_backlog = 16384
net.core.optmem_max = 26214400

# --- TCP BBR & haute concurrence (Caddy / MinIO / FastAPI) ---
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr
net.core.somaxconn = 65535
net.ipv4.tcp_max_syn_backlog = 8192
net.ipv4.tcp_tw_reuse = 1
net.ipv4.ip_local_port_range = 10240 65535
net.ipv4.tcp_keepalive_time = 600
net.ipv4.tcp_keepalive_intvl = 60
net.ipv4.tcp_keepalive_probes = 5

# --- Conntrack (WebRTC UDP crée de nombreuses sessions) ---
net.netfilter.nf_conntrack_max = 262144

# --- Descripteurs & mémoire (Redis / Mongo / Docker) ---
fs.file-max = 2097152
vm.overcommit_memory = 1
vm.swappiness = 10
EOF

sysctl --system
sysctl net.ipv4.tcp_congestion_control
# Attendu : net.ipv4.tcp_congestion_control = bbr
```

### 3.6 Pare-feu UFW

```bash
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp     comment 'SSH'
ufw allow 80/tcp     comment 'HTTP Caddy ACME & redirect'
ufw allow 443/tcp    comment 'HTTPS Caddy'
ufw allow 443/udp    comment 'HTTP/3 QUIC Caddy'
ufw allow 7881/tcp   comment 'LiveKit WebRTC ICE/TCP'
ufw allow 7882/udp   comment 'LiveKit WebRTC UDP media'
# Si vous activez le TURN intégré LiveKit (recommandé en prod) :
ufw allow 3478/udp   comment 'LiveKit TURN UDP'
ufw allow 5349/tcp   comment 'LiveKit TURN TLS'

ufw --force enable
ufw status verbose
```

> **Avertissement sécurité Docker & UFW :** Docker crée ses propres règles `iptables` qui contournent UFW pour tout port mappé sur `0.0.0.0`. Dans `docker-compose.yml`, les ports sensibles (`7880`, `8001`, `9000`, `9001`, `3000`, `3001`) sont déjà restreints à `127.0.0.1:PORT:PORT`. **Ne retirez jamais le préfixe `127.0.0.1:`** de ces services.

### 3.7 Installation et tuning Docker

```bash
curl -fsSL https://get.docker.com | sh
usermod -aG docker deploy
```

Configurez `/etc/docker/daemon.json` pour activer la rotation globale des logs, BuildKit et des `ulimits` adaptés à LiveKit/Mongo/Nginx :

```bash
cat <<'EOF' | tee /etc/docker/daemon.json
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  },
  "live-restore": true,
  "default-ulimits": {
    "nofile": {
      "Name": "nofile",
      "Hard": 65536,
      "Soft": 65536
    }
  }
}
EOF

systemctl restart docker
docker --version && docker compose version
```

Vérifiez également la taille de `/dev/shm` (utilisé par `vl-egress` via `ipc: host`) :

```bash
df -h /dev/shm
# Doit afficher au moins 2 Go (sur un VPS 8 Go, Ubuntu alloue 4 Go par défaut).
```

---

## 4. Installation du projet & Configuration `.env`

Connectez-vous désormais avec l'utilisateur **`deploy`** :

```bash
sudo mkdir -p /opt/videolog && sudo chown deploy:deploy /opt/videolog
cd /opt/videolog
git clone <URL_DE_VOTRE_DEPOT> .
```

### 4.1 Secrets Firebase

Déposez votre fichier de compte de service Firebase sur le VPS (via `scp` depuis votre poste) :

```bash
mkdir -p /opt/videolog/secrets
# Depuis votre poste local :
# scp firebase-service-account.json deploy@<IP_VPS>:/opt/videolog/secrets/firebase-service-account.json

chmod 750 /opt/videolog/secrets
chmod 640 /opt/videolog/secrets/firebase-service-account.json
```

### 4.2 Génération des secrets forts

> **Important MongoDB :** `MONGO_ROOT_PASSWORD` et `MONGO_APP_PASSWORD` sont interpolés directement dans l'URI `mongodb://user:pass@mongo:27017/...`. Utilisez **uniquement des caractères alphanumériques** (`A-Z`, `a-z`, `0-9`) pour éviter toute erreur de parsing URI.

```bash
echo "LIVEKIT_API_KEY=API$(openssl rand -hex 12)"
echo "LIVEKIT_API_SECRET=$(openssl rand -hex 24)"
echo "MINIO_ROOT_USER=vlminio$(openssl rand -hex 4)"
echo "MINIO_ROOT_PASSWORD=$(openssl rand -hex 24)"
echo "MONGO_ROOT_PASSWORD=$(openssl rand -hex 20)"
echo "MONGO_APP_PASSWORD=$(openssl rand -hex 20)"
```

Si vous utilisez aussi le dashboard opérateur legacy (`dashboard.<DOMAIN>`), générez son hash bcrypt Caddy :

```bash
docker run --rm caddy:2.8-alpine caddy hash-password --plaintext 'VOTRE_MOT_DE_PASSE_FORT'
```

### 4.3 Fichier `.env` de production

Créez `/opt/videolog/.env` puis verrouillez ses permissions (`chmod 600 .env`) :

```dotenv
# ── Domaine & URLs publiques ──────────────────────────────────
DOMAIN=videolog.app
APP_PUBLIC_URL=https://app.videolog.app
LIVEKIT_PUBLIC_URL=wss://livekit.videolog.app
MINIO_PUBLIC_URL=https://minio.videolog.app

# ── LiveKit (CRITIQUE sur VPS derrière NAT / Docker bridge) ───
LIVEKIT_API_KEY=<CLE_GENEREE>
LIVEKIT_API_SECRET=<SECRET_GENERE>
LIVEKIT_USE_EXTERNAL_IP=true
LIVEKIT_NODE_IP=<IP_PUBLIQUE_IPV4_DU_VPS>

# ── Firebase Backend ──────────────────────────────────────────
FIREBASE_PROJECT_ID=votre-project-id
FIREBASE_SECRETS_DIR=./secrets

# ── Firebase Frontend (BUILD-TIME : remplir AVANT docker build) ─
VITE_FIREBASE_API_KEY=AIza...
VITE_FIREBASE_AUTH_DOMAIN=votre-project-id.firebaseapp.com
VITE_FIREBASE_PROJECT_ID=votre-project-id
VITE_FIREBASE_APP_ID=1:...:web:...

# ── MongoDB (secrets alphanumériques uniquement) ──────────────
MONGO_INITDB_DATABASE=videolog
MONGO_ROOT_USER=vlroot
MONGO_ROOT_PASSWORD=<SECRET_ALPHA_NUM>
MONGO_APP_USER=videolog_app
MONGO_APP_PASSWORD=<SECRET_ALPHA_NUM>

# ── MinIO S3 ──────────────────────────────────────────────────
MINIO_ROOT_USER=<USER_GENERE>
MINIO_ROOT_PASSWORD=<SECRET_GENERE>

# ── Sécurité & Rooms ──────────────────────────────────────────
LEGACY_OPEN_ROOMS=false
RECORDING_MODE=manual
RECORDING_MAX_DURATION_SECONDS=180
STOP_GRACE_DELAY=10
RECORD_MIN_PARTICIPANTS=2
TELEMETRY_INTERVAL=0.5

# ── Dashboard Legacy (garder les simples quotes autour du hash) ─
DASHBOARD_AUTH_USER=admin
DASHBOARD_AUTH_HASH='$2a$14$...'

# ── Alertes télémétrie par défaut ─────────────────────────────
VIDEOLOG_ROOM=poc-room
VIDEOLOG_ROOMS=poc-room
ALERT_HR_MAX=185
ALERT_HR_REST=50
ALERT_SPEED_MAX=40

# ── SMTP Production (ex. Brevo / Resend / Postmark) ───────────
SMTP_HOST=smtp-relay.brevo.com
SMTP_PORT=587
SMTP_USER=votre-login-smtp
SMTP_PASSWORD=votre-cle-smtp
SMTP_FROM=Videolog <noreply@videolog.app>
SMTP_USE_TLS=false
```

> **3 pièges classiques à éviter dans `.env` :**
> 1. **`LIVEKIT_USE_EXTERNAL_IP=true` et `LIVEKIT_NODE_IP=<IP_PUBLIQUE>`** sont indispensables en prod (ils ne figurent pas dans `.env.example`, mais sont bien lus par `docker-compose.yml`). Sans eux, LiveKit annonce son IP Docker privée `172.x.x.x` et WebRTC échoue sur mobile 4G/5G.
> 2. **`VITE_FIREBASE_*`** sont compilés dans le bundle Vite au moment du `docker compose build backoffice`. Si vous les modifiez plus tard, il faut **rebuilder** `backoffice` (`--no-cache`).
> 3. **`DASHBOARD_AUTH_HASH`** contient des `$` : entourez impérativement la valeur de **simples quotes `'...'`** pour empêcher Docker Compose de les interpréter comme des variables.

---

## 5. Configuration Haute Performance (`docker-compose.prod.yml` & `Caddyfile`)

Les limites du fichier `docker-compose.yml` par défaut sont calibrées pour un environnement léger/POC (MongoDB `512m` / cache `0.25 GB`, Uvicorn 1 worker, Egress sans limite mémoire).

Créez un fichier d'override **`/opt/videolog/docker-compose.prod.yml`** (hors git ou suivi sur une branche déploiement) pour débloquer les performances réelles de votre VPS **sans modifier le fichier de dev**.

### 5.1 Fichier `docker-compose.prod.yml` (calibré pour VPS 8 vCPU / 16 Go RAM)

> *Pour un VPS **4 vCPU / 8 Go RAM**, adaptez simplement : `dashboard-api` → `--workers 2` et `memory: 1g`, `mongo` → `--wiredTigerCacheSizeGB 1` et `memory: 2g`, `egress` → `cpus: '3.0'` et `memory: 2560m`, `livekit` → `memory: 1g`.*

```yaml
# docker-compose.prod.yml — Override Production Haute Performance (8 vCPU / 16 Go)
# Lancement :
#   docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile proxy up -d --build

services:
  caddy:
    # Évite le blocage si le profil `legacy` (service `dashboard`) n'est pas démarré
    depends_on: !override
      - dashboard-api
      - backoffice
      - livekit
      - minio
    deploy:
      resources:
        limits:
          memory: 256m

  livekit:
    ports:
      - "127.0.0.1:7880:7880"
      - "7881:7881"
      - "7882:7882/udp"
      - "3478:3478/udp"    # TURN UDP intégré
      - "5349:5349/tcp"    # TURN TLS intégré (traverse pare-feu 4G/entreprise)
    environment:
      LIVEKIT_CONFIG: |
        port: 7880
        rtc:
          tcp_port: 7881
          udp_port: 7882
          use_external_ip: true
          node_ip: "${LIVEKIT_NODE_IP:?}"
          enable_loopback_candidate: false
        turn:
          enabled: true
          domain: "turn.${DOMAIN:?}"
          udp_port: 3478
          tls_port: 5349
          external_tls: false
        redis:
          address: redis:6379
        keys:
          "${LIVEKIT_API_KEY:?}": "${LIVEKIT_API_SECRET:?}"
        logging:
          level: info
          pion_level: error
        development: false
        room:
          auto_create: true
          max_participants: 25
          empty_timeout: 30
        webhook:
          urls:
            - http://egress-webhook:8088/webhook/livekit
            - http://dashboard-api:8000/webhook/livekit
          api_key: "${LIVEKIT_API_KEY:?}"
    deploy:
      resources:
        limits:
          memory: 2g

  redis:
    command: >
      redis-server
      --appendonly yes
      --appendfsync everysec
      --save ""
      --maxmemory 512mb
      --maxmemory-policy noeviction
      --activedefrag yes
      --aof-use-rdb-preamble yes
    deploy:
      resources:
        limits:
          memory: 640m

  mongo:
    # WiredTiger = ~25-30% de la RAM dispo pour Mongo (3 Go cache dans 4 Go limite)
    command:
      - "mongod"
      - "--wiredTigerCacheSizeGB"
      - "2.5"
      - "--bind_ip_all"
      - "--quiet"
    deploy:
      resources:
        limits:
          memory: 4g

  minio:
    environment:
      MINIO_API_REQUESTS_MAX: "200"
    deploy:
      resources:
        limits:
          memory: 1536m

  egress:
    deploy:
      resources:
        limits:
          cpus: "4.0"
          memory: 3584m   # Garde-fou anti-OOM hôte pour Chromium headless

  dashboard-api:
    # dashboard-api est 100% stateless (Redis PubSub pour SSE, Redis pour rate-limit)
    # 4 workers parallélisent le parsing FIT/GPX, NumPy PMC, fpdf2 PDF et Firebase Auth
    command:
      - "python"
      - "-u"
      - "-m"
      - "uvicorn"
      - "api:app"
      - "--host"
      - "0.0.0.0"
      - "--port"
      - "8000"
      - "--workers"
      - "4"
      - "--proxy-headers"
      - "--forwarded-allow-ips=*"
      - "--limit-concurrency"
      - "200"
      - "--timeout-keep-alive"
      - "30"
    deploy:
      resources:
        limits:
          memory: 1536m

  worker:
    deploy:
      resources:
        limits:
          memory: 512m

  mongo-writer:
    environment:
      # 200 docs/batch divise par 4 les IOPS MongoDB en session live
      BATCH_SIZE: "200"
      FLUSH_INTERVAL: "0.5"
    deploy:
      resources:
        limits:
          memory: 384m

  telemetry-agent:
    deploy:
      resources:
        limits:
          memory: 384m

  backoffice:
    deploy:
      resources:
        limits:
          memory: 128m
```

> **Note sur TURN TLS (port 5349) :** si vous activez `turn.tls_port: 5349` avec `external_tls: false`, LiveKit attend des certificats `cert_file` / `key_file` (vous pouvez monter le volume `caddy_data` en lecture seule pour réutiliser le certificat Let's Encrypt de `turn.<DOMAIN>` généré par Caddy, ou commencer par `turn.udp_port: 3478` sans TLS + `tcp_port: 7881` qui résout déjà 95 % des NAT mobiles). Pour un TURN/TLS complet via le cert Caddy :
> 1. Ajoutez un bloc `turn.{$DOMAIN} { respond "OK" 200 }` dans `Caddyfile` afin que Caddy renouvelle le certificat automatiquement.
> 2. Montez `caddy_data:/caddy-certs:ro` dans `livekit` et configurez `cert_file: /caddy-certs/caddy/certificates/acme-v02.api.letsencrypt.org-directory/turn.${DOMAIN}/turn.${DOMAIN}.crt` (et `.key`).

### 5.2 Optimisation de `egress/config.yaml`

Pour éviter qu'un 2e enregistrement simultané ne sature le CPU et ne provoque des saccades sur le premier, ajoutez un plafond de coût CPU dans `egress/config.yaml` :

```yaml
ws_url: ws://livekit:7880

redis:
  address: redis:6379

tmp_dir: /tmp/egress
health_port: 8080

cpu_cost:
  room_composite_cpu_cost: 3.0
  track_composite_cpu_cost: 1.5
  track_cpu_cost: 0.5

logging:
  level: info
```

### 5.3 Optimisation de `caddy/Caddyfile` pour la production

Améliorations recommandées sur `caddy/Caddyfile` :
1. **Compression `encode zstd gzip`** sur `app.` et `api.` : réduit de **70 à 85 %** la taille des réponses JSON (flux 1 Hz d'activités, séries PMC, annuaire clubs, rapports).
2. **Redirection racine** `https://<DOMAIN>` → `https://app.<DOMAIN>` (le back-office moderne plutôt que l'ancien dashboard).
3. **En-têtes de sécurité HTTP** (`HSTS`, `X-Content-Type-Options`, `Referrer-Policy`).
4. **Exposition `/rooms/*`** sur `api.<DOMAIN>` (si le mobile interroge `/rooms/{room_name}/lobby`).

```caddyfile
(security_headers) {
	header {
		Strict-Transport-Security "max-age=31536000; includeSubDomains"
		X-Content-Type-Options "nosniff"
		Referrer-Policy "strict-origin-when-cross-origin"
		-Server
	}
}

{$DOMAIN} {
	redir https://app.{$DOMAIN}{uri} permanent
}

app.{$DOMAIN} {
	import security_headers
	encode zstd gzip

	@api path /api/v1/* /api/token /api/mobile/* /api/rooms/*
	handle @api {
		uri strip_prefix /api
		reverse_proxy dashboard-api:8000
	}
	handle {
		reverse_proxy backoffice:80
	}
}

livekit.{$DOMAIN} {
	reverse_proxy livekit:7880
}

api.{$DOMAIN} {
	import security_headers
	encode zstd gzip

	@mobile_api path /token /mobile/* /v1/* /rooms/*
	handle @mobile_api {
		reverse_proxy dashboard-api:8000
	}
	@livekit_webhook path /webhook/livekit
	handle @livekit_webhook {
		reverse_proxy dashboard-api:8000
	}
	handle {
		respond "Not Found" 404
	}
}

minio.{$DOMAIN} {
	# Ne pas compresser les flux vidéo MP4 déjà compressés (évite surcoût CPU)
	reverse_proxy minio:9000 {
		flush_interval -1
	}
}

# Décommenter uniquement si vous démarrez le profil `--profile legacy`
# dashboard.{$DOMAIN} {
# 	basic_auth {
# 		{$DASHBOARD_AUTH_USER} {$DASHBOARD_AUTH_HASH}
# 	}
# 	@api_events path /api/events
# 	handle @api_events {
# 		uri strip_prefix /api
# 		reverse_proxy dashboard-api:8000 {
# 			flush_interval -1
# 		}
# 	}
# 	handle {
# 		reverse_proxy dashboard:80
# 	}
# }
```

### 5.4 Option Scale : déporter le stockage vidéo sur un S3 managé

Le backend (`mongo-writer/storage.py`) supporte nativement tout stockage compatible S3 (**Cloudflare R2** avec egress gratuit, **Scaleway Object Storage**, **OVH Object Storage**, **Backblaze B2**). Dès que le volume vidéo dépasse votre disque NVMe, vous pouvez remplacer MinIO par ces 6 variables dans `dashboard-api` / `worker` (et dans les options S3 d'Egress) :

```env
S3_ENDPOINT=https://s3.fr-par.scw.cloud
S3_PUBLIC_ENDPOINT=https://s3.fr-par.scw.cloud
S3_ACCESS_KEY=...
S3_SECRET_KEY=...
S3_REGION=fr-par
S3_BUCKET=videolog-recordings
S3_FORCE_PATH_STYLE=false
```

---

## 6. Premier Déploiement & Vérifications

### 6.1 Validation et Build

```bash
cd /opt/videolog

# 1. Valider la fusion Compose + .env (doit sortir sans erreur)
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile proxy config --quiet

# 2. Construire les images (le 1er build compile MinIO en Go : ~3 à 5 minutes)
DOCKER_BUILDKIT=1 docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile proxy build

# 3. Démarrer la stack complète
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile proxy up -d

# 4. Vérifier que tous les conteneurs sont "Up (healthy)"
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile proxy ps
```

> **Note sur `vl-minio-init` :** c'est un job ponctuel (`restart: "no"`), il doit afficher `Exited (0)` après avoir créé les buckets privés `recordings` et `segments`.

### 6.2 Index MongoDB additionnel (`notifications`)

Créez l'index manquant sur la collection `notifications` (accélère `GET /v1/me/notifications` et le compteur non lu) :

```bash
docker compose exec -T mongo sh -lc 'mongosh --quiet -u "$MONGO_APP_USER" -p "$MONGO_APP_PASSWORD" --authenticationDatabase videolog videolog --eval "
db.notifications.createIndex({ recipient_uid: 1, created_at: -1 });
db.notifications.createIndex({ recipient_uid: 1, read: 1 });
"'
```

### 6.3 Checklist de vérification (Smoke Tests)

**1. Santé interne sur le VPS :**
```bash
curl -fsS http://127.0.0.1:8001/health | jq .
curl -fsS http://127.0.0.1:8001/egress/status | jq .
docker compose logs --tail=50 caddy livekit dashboard-api mongo-writer worker
```

**2. Routes publiques HTTPS (depuis votre poste ou le VPS) :**
```bash
DOMAIN=videolog.app

# Back-office SPA : doit retourner 200
curl -sS -o /dev/null -w "app: %{http_code}\n" https://app.$DOMAIN/

# Catalogue sports public : doit retourner 200
curl -sS -o /dev/null -w "sports: %{http_code}\n" https://api.$DOMAIN/v1/sports

# Endpoints protégés Firebase : doivent retourner 401 sans token
curl -sS -o /dev/null -w "directory: %{http_code}\n" https://api.$DOMAIN/v1/directory/organizations
curl -sS -o /dev/null -w "join-req: %{http_code}\n" https://api.$DOMAIN/v1/me/join-requests
curl -sS -o /dev/null -w "token: %{http_code}\n" -X POST https://api.$DOMAIN/token

# LiveKit signalisation : doit retourner 200 ("OK")
curl -sS -o /dev/null -w "livekit: %{http_code}\n" https://livekit.$DOMAIN/
```

**3. Test WebRTC réel (critique) :**
1. Connectez un coach sur `https://app.<DOMAIN>` depuis un PC (Wi-Fi) et un athlète depuis l'app mobile Flutter **en 4G/5G (Wi-Fi coupé)**.
2. Vérifiez que la vidéo + audio + télémétrie passent dans les deux sens et que les zones FC personnalisées s'affichent.
3. Lancez un enregistrement Egress 15 secondes, stoppez-le, puis vérifiez la lecture MP4 signée depuis `https://minio.<DOMAIN>/...`.
4. Dans `Performance`, cliquez sur **Télécharger PDF** pour valider `fpdf2` en prod.

**4. Vérification DNS Email (si SMTP activé) :**
Assurez-vous d'avoir configuré **SPF**, **DKIM** et **DMARC** chez votre registrar pour `DOMAIN`, puis testez une invitation club et une demande d'adhésion mobile.

---

## 7. Quick-Wins Performance identifiés dans le code (Optionnel mais recommandé)

Lors de l'audit du dépôt pour la mise en production, 4 optimisations rapides côté code apportent un gain mesurable supplémentaire :

1. **Activer `uvloop` + `httptools` dans FastAPI (`mongo-writer/requirements-api.txt`)** :
   - Remplacer `uvicorn==0.34.0` par `uvicorn[standard]==0.34.0` (+30–40 % de débit sur les requêtes async et WebSockets/SSE).
2. **Mettre en cache le client S3 (`mongo-writer/storage.py`)** :
   - Actuellement `get_storage()` instancie deux nouveaux clients `boto3.client("s3")` à chaque appel (ex. lors du listage des enregistrements). Ajouter `@functools.lru_cache(maxsize=1)` sur `get_storage()` supprime cet overhead CPU/mémoire.
3. **Ajouter les index `notifications_col` dans `mongo-writer/db.py` (`ensure_indexes`)** :
   - Pour rendre l'index de la section 6.2 permanent et automatiquement créé sur tout nouvel environnement.
4. **Latence Firebase Auth (`mongo-writer/auth_firebase.py`)** :
   - `auth.verify_id_token(..., check_revoked=True)` effectue un appel réseau vers les serveurs Google à chaque requête HTTP (+50 à 150 ms). C'est maximal en sécurité ; si vous constatez une latence API trop élevée en charge, un cache Redis de 60 s sur le statut de révocation par `(uid, iat)` divise par 3 le temps de réponse tout en gardant la révocation quasi-instantanée.

---

## 8. Exploitation, Sauvegardes, Monitoring & Mises à Jour

### 8.1 Sauvegardes automatiques hors VPS (MongoDB + MinIO)

Les volumes Docker ne sont **pas** une sauvegarde. Configurez un remote `rclone` (ex. Backblaze B2, Scaleway S3, Wasabi ou un 2e serveur SFTP) avec `rclone config`, puis installez ce script :

```bash
cat <<'EOF' | sudo tee /usr/local/bin/videolog-backup.sh
#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/videolog"
BACKUP_DIR="/var/backups/videolog"
REMOTE="remote-s3:videolog-backups"   # À adapter à votre remote rclone
DATE="$(date +%Y%m%d-%H%M%S)"

mkdir -p "$BACKUP_DIR"
cd "$APP_DIR"

# 1. Dump compressé MongoDB
docker compose exec -T mongo sh -lc \
  'mongodump --username "$MONGO_INITDB_ROOT_USERNAME" --password "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin --db videolog --archive --gzip' \
  > "$BACKUP_DIR/mongo-$DATE.archive.gz"

# Vérifier que l'archive n'est pas vide
test -s "$BACKUP_DIR/mongo-$DATE.archive.gz"

# 2. Envoi chiffré/distant des dumps MongoDB (rétention 14 jours locale)
rclone copy "$BACKUP_DIR/mongo-$DATE.archive.gz" "$REMOTE/mongo/"
find "$BACKUP_DIR" -name "mongo-*.archive.gz" -mtime +14 -delete

# 3. Synchronisation incrémentale des vidéos MinIO (si MinIO local)
MINIO_DATA_DIR="$(docker volume inspect videolog_minio_data --format '{{.Mountpoint}}' 2>/dev/null || true)"
if [ -n "$MINIO_DATA_DIR" ] && [ -d "$MINIO_DATA_DIR" ]; then
  rclone sync "$MINIO_DATA_DIR/recordings" "$REMOTE/minio-recordings/" --transfers=4
fi

echo "[$(date -Is)] Backup OK: mongo-$DATE.archive.gz"
EOF

sudo chmod 750 /usr/local/bin/videolog-backup.sh
```

Planifiez l'exécution chaque nuit à 03h00 :
```bash
(sudo crontab -l 2>/dev/null; echo "0 3 * * * /usr/local/bin/videolog-backup.sh >> /var/log/videolog-backup.log 2>&1") | sudo crontab -
```

**Test de restauration MongoDB** (à tester au moins une fois !) :
```bash
docker compose exec -T mongo sh -lc \
  'mongorestore --username "$MONGO_INITDB_ROOT_USERNAME" --password "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin --nsInclude="videolog.*" --drop --archive --gzip' \
  < /var/backups/videolog/mongo-YYYYMMDD-HHMMSS.archive.gz
```

### 8.2 Accès sécurisé à la Console MinIO (sans l'exposer)

Ne publiez jamais le port `9001` sur Internet. Ouvrez un tunnel SSH depuis votre poste local :
```bash
ssh -L 9001:127.0.0.1:9001 deploy@<IP_VPS>
# Puis ouvrir http://localhost:9001 dans votre navigateur
```

### 8.3 Monitoring & Supervision

- **Sonde externe gratuite** (Uptime Kuma / BetterStack / Healthchecks.io) toutes les 60 s sur :
  - `https://app.<DOMAIN>/` (attendu `200`)
  - `https://api.<DOMAIN>/v1/sports` (attendu `200`)
  - `https://livekit.<DOMAIN>/` (attendu `200`)
- **Commandes de diagnostic rapide sur le VPS :**
  ```bash
  # Consommation CPU / RAM en direct par conteneur
  docker stats --no-stream

  # Espace disque (alerter dès 80% — les MP4 MinIO et /tmp/egress remplissent vite)
  df -h / /var/lib/docker

  # Mémoire Redis & taille des streams télémétrie
  docker exec vl-redis redis-cli INFO memory | grep -E "used_memory_human|maxmemory_human"
  docker exec vl-redis redis-cli XLEN telemetry:stream
  docker exec vl-redis redis-cli XLEN telemetry:dlq

  # Taille des collections MongoDB
  docker compose exec -T mongo sh -lc 'mongosh --quiet -u "$MONGO_APP_USER" -p "$MONGO_APP_PASSWORD" --authenticationDatabase videolog videolog --eval "db.getCollectionNames().forEach(c => print(c, Math.round(db[c].stats().storageSize/1048576), \"MB\"))"'
  ```

### 8.4 Procédure de mise à jour (sans perte de données)

```bash
cd /opt/videolog

# 1. Sauvegarde préventive
sudo /usr/local/bin/videolog-backup.sh

# 2. Récupérer le code (ne touche pas à .env ni aux volumes)
git pull --ff-only
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile proxy config --quiet

# 3. Reconstruire et relancer sans jamais utiliser `down -v`
DOCKER_BUILDKIT=1 docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile proxy up -d --build --remove-orphans

# 4. Vérifier l'état et nettoyer les vieilles images
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile proxy ps
docker image prune -f
```

**Rollback rapide en cas de régression :**
```bash
git checkout <COMMIT_PRECEDENT>
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile proxy up -d --build
```

---

## 9. Dépannage rapide (Troubleshooting)

| Symptôme | Cause la plus fréquente | Solution |
|---|---|---|
| Vidéo LiveKit noire ou blocage ICE sur 4G/5G | `LIVEKIT_USE_EXTERNAL_IP` à `false` ou port `7882/udp` bloqué | Mettre `LIVEKIT_USE_EXTERNAL_IP=true` + `LIVEKIT_NODE_IP=<IP_VPS>`, vérifier `ufw status` et activer TURN (`3478/udp`, `5349/tcp`). |
| `caddy` refuse de démarrer (`depends on disabled service dashboard`) | Le service `dashboard` est sous `profiles: ["legacy"]` | Utiliser le `docker-compose.prod.yml` ci-dessus (avec `depends_on: !override`) ou ajouter `--profile legacy`. |
| Login Google échoue sur `app.<DOMAIN>` | Domaine absent dans Firebase Auth | Ajouter `app.<DOMAIN>` dans *Firebase Console → Authentication → Settings → Authorized domains*. |
| Back-office charge une page blanche / erreur Firebase | `VITE_FIREBASE_*` absents lors du `docker compose build` | Renseigner `VITE_FIREBASE_*` dans `.env` puis `docker compose build --no-cache backoffice && docker compose up -d backoffice`. |
| Erreur `401` ou rejet tokens Firebase | Horloge VPS décalée (> 10 s) | Vérifier `chronyc tracking` et redémarrer `chrony`. |
| Enregistrement Egress qui fige ou tue le VPS | Saturation CPU ou RAM Chromium | Limiter `room_composite_cpu_cost: 3.0` dans `egress/config.yaml`, définir `memory: 3584m` sur `egress`, vérifier `df -h /dev/shm`. |
| Erreur `Invalid URI` MongoDB au démarrage | Caractères spéciaux (`@`, `:`, `/`, `#`) dans le mot de passe Mongo | Utiliser uniquement des secrets alphanumériques générés par `openssl rand -hex 20`. |
| Lecture vidéo MP4 échoue dans le navigateur | `MINIO_PUBLIC_URL` resté sur `http://localhost:9000` | Mettre `MINIO_PUBLIC_URL=https://minio.<DOMAIN>` et redémarrer `dashboard-api`, `egress-webhook`, `worker`. |
