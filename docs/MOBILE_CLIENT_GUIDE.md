# Guide d'integration du client mobile Videolog

Ce document décrit l'authentification mobile Firebase, le contrôle d'enregistrement Egress, la connexion LiveKit et la télémétrie binaire.

## URLs de production

| Usage | URL |
|---|---|
| API mobile | `https://api.videolog.app` |
| Connexion LiveKit | `wss://livekit.videolog.app` |
| Dashboard opérateur | `https://dashboard.videolog.app` |
| Fichiers MinIO | `https://minio.videolog.app` |

Le dashboard utilise HTTP Basic. L'API mobile utilise un Firebase ID token en `Authorization: Bearer`; ne mettez jamais les identifiants Basic ou les secrets serveur dans l'application.

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

Pour ce POC, tout utilisateur Firebase authentifié peut lister les rooms non terminées et recevoir un token LiveKit pour rejoindre une room existante non terminée. Le token autorise actuellement la publication et la réception des pistes.

Les contrôles Egress restent réservés aux utilisateurs autorisés dans le document MongoDB `videolog.room_state` de la session :

- `owner_uid`: UID Firebase propriétaire ;
- `recording_operator_uids`: tableau des UID autorisés à contrôler Egress ;
- `participant_uids`: UID participants conservés pour le contrôle métier ;
- `recording_admin: true`: custom claim Firebase réservé aux administrateurs de confiance, avec accès à toutes les sessions.

Pour les sessions existantes, un administrateur peut affecter les UID via un processus d'administration sûr. N'acceptez jamais ces UID depuis une requête mobile non autorisée. Le token LiveKit utilise l'UID Firebase vérifié comme identité et expire après dix minutes.

## Parcours de connexion

1. Le backend ou un opérateur autorisé prépare la session dans MongoDB.
2. L'utilisateur se connecte avec son adresse email et son mot de passe via Firebase Authentication dans l'application. Firebase renvoie un ID token ; le mot de passe n'est jamais envoyé à l'API Videolog.
3. Chaque utilisateur authentifié demande son token LiveKit à `/token` avec le nom d'une room existante et non terminée.
4. Le backend signe le token avec ses clés LiveKit. Ces clés secrètes ne sont jamais embarquées dans l'application.
5. Le client se connecte au SDK LiveKit avec les champs `url` et `token` reçus.
6. LiveKit crée la room à la première connexion autorisée, car `auto_create` est activé. Les autres participants rejoignent en demandant un token avec exactement le même nom de room.

Le endpoint `POST /rooms` existant reste une route d'administration. Il ne doit pas être utilisé comme endpoint public d'authentification ou de création arbitraire de sessions.

### Lister les rooms disponibles sur mobile

L'application peut appeler `GET /mobile/rooms` avec le Firebase ID token en Bearer. Pour ce POC, tout utilisateur authentifié peut voir toutes les rooms non terminées et le champ `can_control_recording` vaut toujours `true` pour l'interface. Les endpoints qui démarrent ou arrêtent réellement Egress vérifient encore les autorisations dans MongoDB. En production, rétablir pour ce champ la règle fondée sur `recording_admin`, `owner_uid` et `recording_operator_uids`. Le paramètre `limit` vaut 50 par défaut et accepte de 1 à 100.

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

**Limite connue du bearing v2 :** le champ ne fait qu'un octet et `255` signifie null. Avec l'encodeur actuel qui borne d'abord à 360 puis à 255, les valeurs de 255 à 360 sont donc décodées comme null. Pour le contrat actuel, envoyer un bearing de 0 à 254 ou null. Représenter correctement 0 à 359 nécessite une nouvelle version de paquet, partagée par le mobile et le serveur ; ne changez pas ce champ silencieusement.

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

Le test `GET /token` doit répondre `405` car la route accepte `POST` uniquement. Un ID token absent ou invalide doit recevoir `401`; une room absente ou terminée doit recevoir `404`. Les contrôles Egress restent soumis à autorisation et peuvent répondre `403`. En mode `automatic`, les routes mobiles de contrôle Egress doivent répondre `409`.
