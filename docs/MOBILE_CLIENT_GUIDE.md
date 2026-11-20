# Guide d'integration du client mobile Videolog

Ce document décrit l'authentification mobile Firebase, le contrôle d'enregistrement Egress, la connexion LiveKit et la télémétrie binaire.

## URLs de production

| Usage | Production | Développement local |
|---|---|---|
| API mobile & Back-office (`/v1/*`, `/token`, `/mobile/*`) | `https://api.videolog.app` | `http://10.0.2.2:8001` (émulateur Android) / `http://localhost:8001` |
| Back-office Web | `https://app.videolog.app` *(ou domaine configuré)* | `http://localhost:3001` |
| Connexion LiveKit | `wss://livekit.videolog.app` | `ws://10.0.2.2:7880` / `ws://localhost:7880` |
| Dashboard opérateur (legacy) | `https://dashboard.videolog.app` | — |
| Fichiers MinIO | `https://minio.videolog.app` | — |

Le dashboard opérateur legacy utilise HTTP Basic. L'API mobile et le back-office utilisent un Firebase ID token en `Authorization: Bearer <id_token>` ; ne mettez jamais les identifiants Basic ou les secrets serveur dans l'application.

`DOMAIN` vaut `videolog.app`. `LIVEKIT_PUBLIC_URL` doit être `wss://livekit.videolog.app` et `MINIO_PUBLIC_URL` `https://minio.videolog.app`. Le serveur renvoie l'URL LiveKit dans la réponse du token. Le client doit utiliser le champ `url` retourné, et ne pas conserver `ws://10.0.2.2:7880` : `10.0.2.2` ne désigne que l'hôte vu depuis un émulateur Android.

## Configuration serveur Firebase

1. Créez ou sélectionnez le projet Firebase utilisé par l'application et activez **Email/Password** dans Authentication > Sign-in method.
2. Créez un compte de service Firebase Admin pour le backend. Déposez son JSON sur le serveur dans `secrets/firebase-service-account.json` (ou configurez `FIREBASE_SECRETS_DIR` vers un dossier protégé). Ce dossier est monté en lecture seule dans `dashboard-api` et ignoré par Git. Ne distribuez jamais ce fichier avec l'application mobile.
3. Configurez `FIREBASE_PROJECT_ID` et `FIREBASE_SECRETS_DIR` dans `.env`.
4. Ajoutez un enregistrement DNS `A` pour `api.<domaine>` vers le VPS, puis redémarrez Caddy et l'API.
5. `RECORDING_MODE=manual` est le mode par défaut : il désactive le démarrage/arrêt selon le nombre de participants. Ne le remplacez par `automatic` que si vous souhaitez réactiver explicitement ce comportement.
6. `RECORDING_MAX_DURATION_SECONDS=180` limite toutes les captures Egress à trois minutes. Le service `egress-webhook` demande l'arrêt à LiveKit à l'échéance, même si le mobile ne l'a pas demandé. Les échéances sont persistées pour que la limite survive au redémarrage du service.

Le compte de service est utilisé uniquement côté backend pour vérifier les ID tokens Firebase et leur révocation. Si sa configuration manque, les routes mobiles répondent `503`; elles ne retombent pas sur une authentification moins sûre.

## Autorisation par session

L'accès aux sessions est isolé par organisation (`org_id`) et par liste de participants dans `videolog.room_state` :

- Un utilisateur authentifié ne voit dans `GET /mobile/rooms` et ne peut rejoindre via `POST /token` que les sessions actives de son organisation auxquelles il participe (ou qu'il encadre en tant que coach/admin/owner).
- Le champ `can_control_recording` reflète les droits réels : `true` pour l'`owner` ou `admin` de l'organisation, ou le `coach` propriétaire/opérateur de la session ; `false` pour un `athlete` (les appels start/stop renvoient `403`).
- Les anciennes rooms sans `org_id` sont refusées par défaut (`403`), sauf si `LEGACY_OPEN_ROOMS=true` est activé pour une phase de transition ou si elles sont rattachées via `scripts/attach_legacy_rooms.py`.
- Le token LiveKit utilise l'UID Firebase comme `identity`, le nom complet comme `name` et inclut `{role, org_id}` dans `metadata` ; il expire après dix minutes.

## Parcours de connexion

1. Le backend ou un opérateur autorisé prépare la session dans MongoDB.
2. L'utilisateur se connecte avec son adresse email et son mot de passe via Firebase Authentication dans l'application. Firebase renvoie un ID token ; le mot de passe n'est jamais envoyé à l'API Videolog.
3. Chaque utilisateur authentifié demande son token LiveKit à `/token` avec le nom d'une room existante et non terminée.
4. Le backend signe le token avec ses clés LiveKit. Ces clés secrètes ne sont jamais embarquées dans l'application.
5. Le client se connecte au SDK LiveKit avec les champs `url` et `token` reçus.
6. LiveKit crée la room à la première connexion autorisée, car `auto_create` est activé. Les autres participants rejoignent en demandant un token avec exactement le même nom de room.

Le endpoint `POST /rooms` existant reste une route d'administration. Il ne doit pas être utilisé comme endpoint public d'authentification ou de création arbitraire de sessions.

### Lister les rooms disponibles sur mobile

L'application appelle `GET /mobile/rooms` avec le Firebase ID token en Bearer. Le backend retourne uniquement les rooms actives (non terminées) de l'organisation de l'utilisateur auxquelles il participe (ou qu'il encadre en tant que `coach` / `admin` / `owner`). Le champ `can_control_recording` vaut `true` uniquement pour les utilisateurs habilités (`owner`, `admin`, `coach` propriétaire ou opérateur, ou `recording_admin`), et `false` pour un `athlete`. Le paramètre `limit` vaut 50 par défaut et accepte de 1 à 100.

Voir le [workflow mobile complet](./MOBILE_RECORDING_WORKFLOW.md) pour l'exemple Flutter et le détail de la réponse.

### Connexion Flutter avec email et mot de passe

Configurez Firebase dans l'application Flutter avec FlutterFire, puis ajoutez `firebase_auth`. La connexion se fait directement auprès de Firebase :

```dart
import 'package:firebase_auth/firebase_auth.dart';

final credential = await FirebaseAuth.instance.signInWithEmailAndPassword(
  email: email.trim(),
  password: password,
);

final user = credential.user;
if (user == null) {
  throw StateError('Firebase n\'a pas renvoyé d\'utilisateur connecté');
}

final idToken = await user.getIdToken();
if (idToken == null) {
  throw StateError('Impossible de récupérer le Firebase ID token');
}
```

Utilisez ensuite cet ID token dans l'en-tête `Authorization` de type Bearer pour les appels à `api.videolog.app`. N'envoyez jamais l'email ou le mot de passe à l'API Videolog, ne stockez pas le mot de passe dans l'application et ne journalisez pas l'ID token. Firebase gère la session ; après la connexion, récupérez l'ID token courant avec `FirebaseAuth.instance.currentUser?.getIdToken()` pour les requêtes API.

## Récupérer un token

Requête HTTP :

```http
POST https://api.videolog.app/token
Content-Type: application/json
Authorization: Bearer <Firebase ID token>
```

Corps JSON :

```json
{
  "room": "session-2026-10-25-a"
}
```

Exemple Flutter avec le package `http` :

```dart
final currentUser = FirebaseAuth.instance.currentUser;
if (currentUser == null) {
  throw StateError('Utilisateur Firebase non connecté');
}
final idToken = await currentUser.getIdToken();
if (idToken == null) {
  throw StateError('Impossible de récupérer le Firebase ID token');
}

final response = await http.post(
  Uri.parse('https://api.videolog.app/token'),
  headers: {
    'Content-Type': 'application/json',
    'Authorization': 'Bearer $idToken',
  },
  body: jsonEncode({
    'room': roomName,
  }),
);

if (response.statusCode != 200) {
  throw Exception('Token endpoint HTTP ${response.statusCode}');
}

final result = jsonDecode(response.body) as Map<String, dynamic>;
if (result['error'] != null || result['token'] == null || result['url'] == null) {
  throw Exception('Réponse token invalide');
}

final token = result['token'] as String;
final liveKitUrl = result['url'] as String;
```

Réponse :

```json
{
  "token": "<JWT LiveKit>",
  "room": "session-2026-10-25-a",
  "identity": "<UID Firebase vérifié>",
  "url": "wss://livekit.videolog.app"
}
```

Ne journalisez ni l'ID token Firebase ni le JWT LiveKit. L'identité est dérivée du UID vérifié, jamais du champ `identity` fourni par le client. Le serveur vérifie l'appartenance à la session avant de générer un JWT LiveKit de dix minutes.

## Démarrer, arrêter et suivre Egress

Les trois endpoints mobiles utilisent le même Firebase ID token. Le backend vérifie aussi que l'UID est `owner_uid`, figure dans `recording_operator_uids`, ou possède le custom claim `recording_admin: true`. Le mobile ne transmet jamais les secrets LiveKit, le chemin de stockage ou un `egress_id` pour arrêter.

```http
POST https://api.videolog.app/mobile/rooms/session-2026-10-25-a/recording/start?layout=grid
Authorization: Bearer <Firebase ID token>
```

Le paramètre `layout` accepte `grid`, `speaker` ou `single-speaker`. Réponse :

```json
{
  "room_name": "session-2026-10-25-a",
  "egress_id": "<identifiant>",
  "status": "EGRESS_STARTING"
}
```

```http
POST https://api.videolog.app/mobile/rooms/session-2026-10-25-a/recording/stop
Authorization: Bearer <Firebase ID token>
```

Le serveur retrouve l'Egress actif associé à la room et demande son arrêt. Pour afficher l'état et les liens de lecture signés après finalisation :

```http
GET https://api.videolog.app/mobile/rooms/session-2026-10-25-a/recording
Authorization: Bearer <Firebase ID token>
```

Un même démarrage concurrent est refusé avec `409`; l'application peut afficher l'état de la session via `GET` avant de réessayer. Les événements Egress sont vérifiés par signature LiveKit avant d'être appliqués. La route API HTTPS n'expose que `/token`, `/mobile/*` et le webhook LiveKit ; les autres routes ne sont pas publiées sur ce sous-domaine.

La limite est calculée depuis l'événement `egress_started` et vaut trois minutes par défaut. Le mobile peut arrêter l'enregistrement plus tôt ; si l'appel d'arrêt n'arrive pas (perte réseau, fermeture de l'application), le watchdog serveur appelle quand même `stop_egress`. Une courte latence de balayage réseau/worker est possible après les 180 secondes.

## Connexion et partage de room

Avec le SDK LiveKit Flutter, créez une instance `Room` et connectez-la avec le token obtenu :

```dart
final room = Room();
await room.connect(liveKitUrl, token);
```

Tous les participants doivent utiliser le même `roomName`. Chaque UID Firebase donne une identité distincte dans la room.

Les droits du token sont actuellement identiques pour tous les utilisateurs : publication et réception sont autorisées. Le champ `role` du paquet de télémétrie (`coach`, `trainee`, `observer`) ne change pas les permissions LiveKit.

## Envoyer la télémétrie binaire

Publier les octets sur le DataTrack LiveKit avec le topic exact `telemetry`. Le code client fourni utilise une publication non fiable (`reliable: false`) environ toutes les 500 ms ; des paquets peuvent donc être perdus en cas de congestion. Ne publiez pas les données de posture dans ce paquet.

```dart
room.localParticipant?.publishData(
  packetBytes,
  reliable: false,
  topic: 'telemetry',
);
```

Le serveur accepte la version 2, d'une longueur de 21 octets, en big-endian. Envoyer exactement 21 octets. Les champs optionnels doivent utiliser leurs sentinelles nulles dédiées.

| Offset | Taille | Type | Champ | Encodage / valeur nulle |
|---:|---:|---|---|---|
| 0 | 1 | `uint8` | version | `2` |
| 1 | 1 | `uint8` | rôle | `0=coach`, `1=trainee`, `2=observer` |
| 2 | 2 | `uint16` | vitesse | km/h × 100 ; `65535` = null |
| 4 | 1 | `uint8` | fréquence cardiaque | bpm ; `255` = null |
| 5 | 1 | `uint8` | cadence | rpm ; `255` = null |
| 6 | 4 | `int32` | latitude | degrés × 10 000 000 ; `0x7FFFFFFF` = null |
| 10 | 4 | `int32` | longitude | degrés × 10 000 000 ; `0x7FFFFFFF` = null |
| 14 | 2 | `int16` | altitude | mètres × 100 ; `0x7FFF` = null |
| 16 | 1 | `uint8` | bearing | degrés ; `255` = null |
| 17 | 4 | `uint32` | timestamp de capture | millisecondes Unix modulo `2^32` |

Dans Dart, préciser `Endian.big` sur chaque lecture/écriture `ByteData` rend le contrat explicite. La vitesse envoyée par l'exemple mobile est en km/h (`position.speed * 3.6`) ; le commentaire historique « cm/s » ne correspond pas à l'échelle réellement encodée. La latitude/longitude utilisent un facteur `10^7` (sept décimales), malgré le commentaire historique parlant de huit décimales.

**Limite connue du bearing v2 :** le champ ne fait qu'un octet et `255` signifie null. Avec l'encodeur actuel qui borne d'abord à 360 puis à 255, les valeurs de 255 à 360 sont donc décodées comme null. Pour le contrat actuel v2, envoyer un bearing de 0 à 254 ou null.

### Paquet binaire v3 (25 octets, big-endian — Phase 1)

Le serveur accepte également la **version 3** (25 octets, big-endian), rétrocompatible avec la v2, qui ajoute la **puissance (`power_w`)** et corrige le champ `bearing` sur `uint16` :

| Offset | Taille | Type | Champ | Encodage / valeur nulle |
|---:|---:|---|---|---|
| 0 | 1 | `uint8` | version | `3` |
| 1 | 1 | `uint8` | rôle | `0=coach`, `1=trainee`, `2=observer` |
| 2 | 2 | `uint16` | vitesse | km/h × 100 ; `65535` = null |
| 4 | 1 | `uint8` | fréquence cardiaque | bpm ; `255` = null |
| 5 | 1 | `uint8` | cadence | rpm ; `255` = null |
| 6 | 4 | `int32` | latitude | degrés × 10 000 000 ; `0x7FFFFFFF` = null |
| 10 | 4 | `int32` | longitude | degrés × 10 000 000 ; `0x7FFFFFFF` = null |
| 14 | 2 | `int16` | altitude | mètres × 100 ; `0x7FFF` = null |
| 16 | 2 | `uint16` | bearing | degrés `0..359` ; `65535` = null |
| 18 | 2 | `uint16` | puissance (`power_w`) | watts ; `65535` = null |
| 20 | 4 | `uint32` | timestamp de capture | millisecondes Unix modulo `2^32` |
| 24 | 1 | `uint8` | flags | réservé (`0`) |

### Timestamp et synchronisation vidéo

Le timestamp du paquet est l'heure Unix de capture en millisecondes, tronquée à 32 bits. Le serveur étend cette valeur à l'époque 64 bits la plus proche de son heure de réception. Cela corrige le rebouclage du compteur de 32 bits, mais suppose que l'horloge du mobile est raisonnablement synchronisée avec le VPS. Activer l'heure automatique/NTP sur le téléphone ; un décalage de plus d'environ 24,8 jours est ambigu.

Pour la relecture, l'API calcule `video_ms` par rapport à l'heure de début du fichier Egress (`video_started_at`, avec repli sur `started_at`) à partir de l'heure de capture mobile. Le lecteur doit utiliser `video_ms` pour positionner une mesure sur la vidéo. `pts_ms` représente le temps relatif à l'enregistrement de session côté telemetry-agent et n'est pas le curseur vidéo.

## Traitement côté serveur

- L'agent n'écoute que les DataTracks dont le topic est `telemetry`.
- Les paquets v2 valides sont décodés et les octets originaux sont conservés en Base64 dans MongoDB.
- Les paquets inconnus ou invalides ne sont pas interprétés ; ils restent conservés pour diagnostic.
- Les champs structurés `posture` sont retirés avant stockage.
- L'agent rejoint la room avec l'identité `telemetry-agent`, peut recevoir les données et ne peut pas publier.

## Vérification de l'intégration

1. Vérifier que `POST https://api.videolog.app/token` renvoie `200` et une réponse contenant `token`, `room`, `identity` et `url`.
2. Vérifier que `url` vaut `wss://livekit.videolog.app` après le redéploiement de `dashboard-api` avec le `.env` mis à jour.
3. Connecter deux clients avec la même room et des identités distinctes ; vérifier qu'ils apparaissent dans la même room LiveKit.
4. Publier un paquet v2 sur `telemetry` et vérifier la réception côté telemetry-agent/dashboard.
5. Tester les champs nuls et les coordonnées négatives ; vérifier que le décodage restitue les unités et signes attendus.

Le test `GET /token` doit répondre `405` car la route accepte `POST` uniquement. Un ID token absent ou invalide doit recevoir `401`; une room absente ou terminée doit recevoir `404`. En mode `automatic`, les routes mobiles de contrôle Egress doivent répondre `409`.


---

## Nouveautés Back-office v1 & Phase 2 à intégrer dans le mobile

### 1. Authentification (Email/Mot de passe & Google Sign-In)
- Le backend accepte les tokens Firebase issus d'**Email/Password** et de **Google Sign-In** (`signInWithCredential`).
- **Vérification d'e-mail** : les comptes créés par e-mail/mot de passe doivent avoir `emailVerified == true` pour appeler `POST /v1/me/onboarding` ou `POST /v1/invitations/{token}/accept` (sinon `403`). En Flutter : `await user.sendEmailVerification()`, puis `await user.reload()` et `await user.getIdToken(true)` après validation.

### 2. Profil compte, Onboarding & Invitations (`/v1/me`, `/v1/invitations`)
Après connexion Firebase, l'application mobile doit appeler :

```http
GET /v1/me
Authorization: Bearer <Firebase ID token>
```

La réponse contient :
- `user` : profil utilisateur (`uid`, `email`, `first_name`, `last_name`, `athlete_profile`, `health_consent`, etc.)
- `memberships` : liste des organisations dont l'utilisateur est membre (`org_id`, `role` parmi `owner | admin | coach | athlete`, `organization: { id, name, type }`).

**Cas à gérer côté mobile :**
- **Utilisateur sans membership** :
  - S'il arrive via un **lien d'invitation** (`/invite/<token>`), appeler `GET /v1/invitations/{token}` pour afficher le club/coach invitant, puis `POST /v1/invitations/{token}/accept`.
  - Sinon, rediriger vers un écran d'onboarding ou lui indiquer de créer son espace (club ou coach privé) via `POST /v1/me/onboarding` (ou via le back-office Web).
- **Utilisateur avec plusieurs organisations** : conserver l'`org_id` actif dans l'état de l'application (ou sélecteur d'organisation) pour les appels `/v1/orgs/{org_id}/...`.

### 3. Profil sportif, Restriction 15 ans & Consentement santé RGPD
Pour enregistrer ou mettre à jour le profil sportif d'un athlète (notamment pour activer les **seuils cardiaques personnalisés** dans `telemetry-agent`) :

```http
PUT /v1/me/athlete-profile
Authorization: Bearer <Firebase ID token>
Content-Type: application/json
```

Corps JSON :
```json
{
  "sport": "cyclisme",
  "level": "compétition régionale",
  "birth_date": "2002-05-14",
  "height_cm": 178,
  "weight_kg": 72.5,
  "hr_max": 188,
  "hr_rest": 48,
  "health_consent": true
}
```

**Règles de validation serveur (`422` si non respectées) :**
- `sport` : `"cyclisme"`, `"course à pied"`, `"triathlon"`, `"natation"`, `"aviron"`, `"football"`, `"rugby"`, `"autre"`.
- `level` : `"loisir"`, `"compétition régionale"`, `"nationale"`, `"élite"`.
- **Âge minimum (15 ans)** : `birth_date` doit correspondre à un âge `>= 15` ans (sinon refus `422`).
- **Consentement santé RGPD explicite** : si `weight_kg`, `hr_max` ou `hr_rest` est renseigné, `health_consent` **doit** être `true`.
- **Cohérence FC** : `hr_rest` doit être strictement inférieur à `hr_max`.
- **Révocation** : l'utilisateur peut retirer son consentement à tout moment via `DELETE /v1/me/health-consent` (ce qui purge les données de santé associées et repasse les alertes sur les seuils par défaut).

**Impact temps réel côté `telemetry-agent` :**
- Dès qu'une room démarre (`room_started` publié sur Redis `room:events`), `telemetry-agent` rejoint automatiquement la room et évalue les alertes cardiaques avec `hr_max` / `hr_rest` de l'athlète **uniquement si `health_consent.granted == true`** (sinon repli sur `ALERT_HR_MAX=185` et `ALERT_HR_REST=50`).

### 4. Création de séance & Relecture / Exploitation (`/v1/orgs/{org_id}/sessions`)
- **Lister les sessions** : `GET /v1/orgs/{org_id}/sessions`
- **Créer une session** (`owner | admin | coach`) : `POST /v1/orgs/{org_id}/sessions` avec `{ "title": "...", "group_id": null, "participant_uids": ["..."] }`
- **Détail d'une session + URLs vidéo signées** :
  ```http
  GET /v1/orgs/{org_id}/sessions/{room_name}
  Authorization: Bearer <Firebase ID token>
  ```
  Retourne `room`, `participants` (nom, e-mail, rôle) et `recordings` contenant des URLs MP4 présignées MinIO **valables 1 heure** (rafraîchir l'appel si la lecture reprend après expiration).
- **Télémétrie & Alertes de la séance** :
  - `GET /v1/orgs/{org_id}/sessions/{room_name}/telemetry`
  - `GET /v1/orgs/{org_id}/sessions/{room_name}/alerts`
  *(Filtrage RGPD automatique : un `athlete` ne reçoit que ses propres points de télémétrie et alertes.)*
- **Marqueurs coach** :
  - Lecture (coach + athlètes participants) : `GET /v1/orgs/{org_id}/sessions/{room_name}/markers`
  - Création (`owner | admin | coach` uniquement, `403` pour un athlète) : `POST /v1/orgs/{org_id}/sessions/{room_name}/markers` avec `{ "pts_ms": 12500, "label": "Relance côte", "note": "...", "category": "technique" }`
  - Suppression (`owner | admin | coach`) : `DELETE /v1/orgs/{org_id}/sessions/{room_name}/markers/{marker_id}`

### 5. Charte graphique VideoLog (alignement UI mobile / web)
Pour garder une identité visuelle homogène avec le back-office et `vroom.videolog.app` :
- **Couleurs** :
  - Fond principal : `#090B0E` / `#0F1318`
  - Cartes / panneaux : `#151A21` (bordure `#232B35`)
  - Texte principal / secondaire : `#EEF2F6` / `#98A3B0`
  - Accent principal (Cyan VideoLog) : `#18ABF8` (secondaire `#3A8FD6`)
  - Succès / Live OK : `#3DDC97`
  - Enregistrement / Alerte critique : `#E5373A`
- **Typographies** :
  - Titres / KPIs : `TradeGothicBold` (fichier disponible dans `backoffice/public/fonts/TradeGothicBold.ttf`)
  - Corps de texte : `Montserrat`
