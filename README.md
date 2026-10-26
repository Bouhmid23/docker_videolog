# Videolog POC — Guide de démarrage

## Déploiement sur un VPS

La configuration de production utilise Caddy pour HTTPS. Le dashboard est protégé par HTTP Basic ; l'API mobile dédiée exige un ID token Firebase. Les enregistrements MinIO restent privés ; l'API fournit des liens de lecture signés valables une heure.

1. Créer des enregistrements DNS `A` pour `videolog.app`, `dashboard.videolog.app`, `api.videolog.app`, `livekit.videolog.app` et `minio.videolog.app`, tous vers l'adresse IPv4 du VPS.
2. Configurer `.env` avec `DOMAIN=videolog.app`, `LIVEKIT_PUBLIC_URL=wss://livekit.videolog.app`, `MINIO_PUBLIC_URL=https://minio.videolog.app` et les paramètres Firebase. Placer le compte de service Firebase dans le dossier secrets indiqué par `FIREBASE_SECRETS_DIR`. `RECORDING_MODE=manual` désactive le démarrage automatique par nombre de participants. `RECORDING_MAX_DURATION_SECONDS=180` impose un arrêt serveur après trois minutes, même sans commande mobile. Utiliser des secrets alphanumériques forts pour LiveKit, MinIO et MongoDB ; les mots de passe Mongo sont inclus dans les URI.
3. Générer le hash du mot de passe du dashboard avec `docker run --rm caddy:2-alpine caddy hash-password --plaintext 'mot-de-passe-fort'`, puis le renseigner dans `DASHBOARD_AUTH_HASH`. Dans `.env`, entourer la valeur de hash de simples quotes pour que Compose préserve ses `$`.
4. Ouvrir sur le pare-feu du VPS `80/tcp`, `443/tcp`, `443/udp`, `7881/tcp` et `7882/udp`. Les bases, MinIO et l'API restent liées à localhost ou au réseau Docker.
5. Démarrer les services puis le proxy TLS :

```bash
docker compose up -d --build
docker compose --profile proxy up -d
```

Caddy obtient et renouvelle automatiquement les certificats. Le dashboard est disponible sur `https://dashboard.videolog.app` ; l'accès demandera l'utilisateur et le mot de passe configurés. `https://videolog.app` redirige vers le dashboard. L'application mobile utilise `https://api.videolog.app`, les clients LiveKit `wss://livekit.videolog.app` et les liens MinIO `https://minio.videolog.app`.

### Mettre à jour une installation VPS existante

Depuis le VPS, se placer dans le dossier du depot. Cette procedure conserve `.env` et les volumes Docker. Ne pas utiliser `docker compose down -v` : cette commande efface les donnees persistantes.

1. Verifier l'etat et sauvegarder les donnees avant le changement :

```bash
cd /chemin/vers/videolog-poc
docker compose ps
docker compose exec -T mongo sh -lc 'mongodump --username "$MONGO_INITDB_ROOT_USERNAME" --password "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin --db videolog --archive' > "$HOME/videolog-mongo-$(date +%Y%m%d-%H%M%S).archive"
cp .env "$HOME/videolog-env-$(date +%Y%m%d-%H%M%S).bak"
```

La sauvegarde MongoDB utilise les identifiants deja injectes dans le conteneur. Verifier que le fichier archive existe et n'est pas vide. Si l'ancienne base fonctionne sans authentification, retirer les options `--username`, `--password` et `--authenticationDatabase admin` de la commande. Sauvegarder egalement le bucket MinIO `recordings` vers un stockage hors du VPS avant une mise a jour importante.

2. Mettre a jour le code sans remplacer `.env`, puis verifier la configuration Compose :

```bash
git status --short
git pull --ff-only
docker compose config --quiet
```

Le fichier `.env` contient les valeurs propres au déploiement et n'est pas versionné. Vérifier qu'il définit les variables requises dans cette documentation, sans remplacer les secrets de production ni afficher `.env` dans un terminal partagé. La commande `docker compose config --quiet` doit terminer sans erreur. Si `git status` affiche des modifications locales, les sauvegarder et les examiner avant le `git pull`.

3. Reconstruire et demarrer les services, y compris le profil Caddy :

```bash
docker compose --profile proxy up -d --build --remove-orphans
docker compose ps
docker compose logs --since=10m caddy dashboard dashboard-api telemetry-agent mongo-writer
```

`--remove-orphans` retire les anciens conteneurs de services qui ne figurent plus dans le Compose courant, mais ne supprime pas les volumes. Attendre que les services soient sains et verifier l'acces au dashboard avant de fermer la session SSH. Pour revenir au commit precedent, faire `git checkout <commit-precedent>`, puis relancer la commande `up` ci-dessus ; ne pas effacer les volumes.

Depuis un poste ayant acces au domaine, controler que le domaine principal et l'API demandent bien une authentification :

```bash
curl -sS -o /dev/null -w '%{http_code}\n' https://dashboard.videolog.app/
curl -sS -o /dev/null -w '%{http_code}\n' https://dashboard.videolog.app/api/docs
```

Les deux reponses attendues sont `401`. Le domaine `https://videolog.app` redirige vers le dashboard. L'API mobile est sur `https://api.videolog.app` et utilise des ID tokens Firebase, pas HTTP Basic.

Si cette version active l'authentification MongoDB sur une base preexistante creee sans auth, ne pas redemarrer tout le stack tel quel : suivre d'abord la procedure de migration Mongo ci-dessous. L'initialisation Docker ne cree pas les comptes dans un volume Mongo deja initialise.

### Audit de l'authentification HTTP Basic

| Brique / URL | Basic Auth | Protection appliquee |
|---|---|---|
| Dashboard `https://dashboard.videolog.app/` | Oui | Caddy protège l'interface opérateur par HTTP Basic. |
| API dashboard `https://dashboard.videolog.app/api/...` | Oui | Les événements SSE du dashboard passent par le vhost authentifié. |
| API mobile `https://api.videolog.app` | Non | Seuls `/token`, `/mobile/*` et le webhook LiveKit sont routés ; Firebase ID tokens protègent les routes mobiles et le webhook est signé. |
| API directe `127.0.0.1:8001` | Non | Port lie a localhost : pas accessible directement depuis Internet. |
| Dashboard direct `127.0.0.1:3000` | Non | Port lie a localhost : pas accessible directement depuis Internet. |
| LiveKit `livekit.videolog.app` et ports medias 7881/7882 | Non | Le signalement utilise les jetons/API keys LiveKit ; les ports medias publics sont necessaires a WebRTC. Basic Auth HTTP ne convient pas a ces flux. |
| MinIO `minio.videolog.app` | Non | Les buckets sont prives et les liens de lecture sont signes ; l'API S3 utilise les identifiants MinIO. Ce sous-domaine ne doit pas etre protege par un challenge Basic generique, car cela bloquerait les liens video signes. |
| Console MinIO `127.0.0.1:9001` | Non | Port local uniquement ; acces d'administration via tunnel SSH et identifiants MinIO. |
| MongoDB, Redis, webhooks, agents, writer, Egress | Non | Services internes au reseau Docker, sans port public publie. |

**Conclusion :** HTTP Basic protège le dashboard et son API SSE opérateur. L'API mobile s'appuie sur les ID tokens Firebase. LiveKit, MinIO et les ports locaux/internes ont leurs mécanismes propres ; les ports locaux ne doivent pas être republiés sur `0.0.0.0` ni exposés par une règle de pare-feu. Le hash Caddy et l'utilisateur doivent être définis dans `.env` (`DASHBOARD_AUTH_USER` et `DASHBOARD_AUTH_HASH`) ; ne pas laisser le hash vide en production.

### Base MongoDB déjà utilisée

L'authentification MongoDB est activée pour les nouvelles installations. Si `mongo_data` contient déjà une base créée sans authentification, sauvegarder les données, démarrer MongoDB seul avec la nouvelle configuration, puis créer les utilisateurs depuis la connexion locale dans le conteneur :

```bash
docker compose up -d mongo
docker compose exec mongo mongosh
```

Dans `mongosh`, créer un utilisateur racine dans `admin` et un utilisateur `readWrite` dans `videolog`, en reprenant les noms et mots de passe de `.env`. Quitter le shell, puis lancer les autres services. Ne pas supprimer le volume pour contourner cette migration.

### Sauvegardes et capacité

Les volumes Docker assurent la persistance locale, pas une sauvegarde. Configurer des sauvegardes hors du VPS pour MongoDB et MinIO, et vérifier régulièrement qu'elles peuvent être restaurées. Les enregistrements vidéo déterminent principalement le besoin disque ; surveiller l'espace libre et appliquer une politique de rétention adaptée. Egress/Chromium est gourmand en CPU : commencer autour de 4 vCPU et 8 Go de RAM pour une petite charge, puis ajuster après un essai avec le nombre réel de rooms et d'enregistrements simultanés.

Les flux Redis sont limités à 100 000 événements de télémétrie et 10 000 alertes. Si le writer reste en panne et que Redis atteint sa mémoire maximale, les nouvelles écritures échoueront plutôt que d'évincer silencieusement les messages existants ; surveiller les logs et la mémoire Redis.

## Architecture déployée

Pour adapter le client mobile au serveur, au token LiveKit et au format des paquets de télémétrie, voir le [guide d'intégration du client mobile](docs/MOBILE_CLIENT_GUIDE.md).

```
[Mobile Simulator] ──DataTrack──▶ [LiveKit Server] ◀── [Telemetry Agent]
                                                               │
                                                         Redis Streams
                                                               │
                                                        [Mongo Writer]
                                                               │
                                                           MongoDB
```

Les paquets LiveKit du topic `telemetry` au format JSON restent interprétés comme avant. Les paquets binaires mobiles version 2 (21 octets) sont décodés en mesures, tout en conservant les octets reçus dans Redis puis MongoDB en Base64. Les paquets inconnus ou invalides sont conservés sans être interprétés. Les données structurées `posture` sont exclues.

Le lecteur charge les échantillons MongoDB pour l'enregistrement sélectionné et place chaque échantillon sur la timeline vidéo à partir de son timestamp mobile et de l'heure de début du fichier Egress. Les courbes de vitesse de tous les participants apparaissent sur le même graphique, avec une couleur et une légende par identité. Les participants peuvent être filtrés indépendamment pour la carte GPS et les valeurs courantes (vitesse, fréquence cardiaque, cadence et bearing). Le curseur suit la lecture et les sauts de la vidéo. La synchronisation dépend de l'alignement de l'horloge du mobile et de celle du VPS.

## Prérequis

- Docker >= 24
- Docker Compose >= 2.20
- Ports à prévoir : 80/443 pour le proxy, 7881/TCP et 7882/UDP pour LiveKit ; l'interface locale utilise aussi 3000, 8001, 9000 et 9001 sur loopback.

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
| **MinIO Console** | http://localhost:9001 | valeurs `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` de `.env` |
| **MongoDB / Redis** | réseau Docker interne | non publiés sur l'hôte |

## Ce que tu vois dans le dashboard

1. **Stats temps réel** : vitesse, RPM, PTS vidéo, nb d'événements MongoDB
2. **Pipeline status** : etat des services et volumes de donnees
3. **Graphiques** : speed et RPM en série temporelle
4. **Trajectoire GPS** : donnees GPS du mobile
5. **Derniers événements** : table live des docs MongoDB

## Commandes utiles

```bash
# Arrêter
docker compose down


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
├── telemetry-agent/
│   ├── Dockerfile
│   └── agent.py                ← Écoute DataTracks → Redis Streams
├── mongo-writer/
│   ├── Dockerfile              ← Mongo Writer
│   ├── Dockerfile.api          ← Dashboard API
│   ├── writer.py               ← Redis Streams → MongoDB bulk insert
│   ├── api.py                  ← FastAPI REST pour le dashboard
│   └── init.js                 ← Init MongoDB (index)
└── web-dashboard/
    ├── Dockerfile
    └── index.html              ← Dashboard temps réel
```

## Prochaines étapes (post-POC)

- [ ] Ajouter authentification JWT sur tous les endpoints
- [ ] Déployer TURN server (Coturn) pour NAT traversal
- [ ] Cluster LiveKit (2+ nœuds + Redis partagé)
- [ ] MinIO mode distribué (4 nœuds)

---

## Enregistrement avec LiveKit Egress

### Depuis le dashboard (recommandé)
Ouvrir http://localhost:3000 → panneau **Enregistrement Egress** → bouton **Start**.

### Mode automatique optionnel
Le mode automatique est désactivé par défaut (`RECORDING_MODE=manual`) afin que l'enregistrement soit commandé explicitement depuis le mobile ou le dashboard. Pour réactiver l'ancien déclenchement selon le nombre de participants, configurez `RECORDING_MODE=automatic`.

En mode automatique, l'enregistrement démarre lorsqu'au moins deux participants connectés ont publié une télémétrie de rôle `coach` ou `trainee`. Le seuil `RECORD_MIN_PARTICIPANTS` et le délai d'arrêt `STOP_GRACE_DELAY` ne s'appliquent qu'à ce mode.

Quel que soit le mode, `RECORDING_MAX_DURATION_SECONDS=180` impose un arrêt serveur après trois minutes, même si aucun client n'envoie de commande d'arrêt.

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
1. MinIO Console → http://localhost:9001 (identifiants `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` de `.env`)
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
