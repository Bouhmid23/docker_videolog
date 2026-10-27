# Workflow mobile : connexion et enregistrement

Ce document décrit le parcours de l'application Flutter pour se connecter à Videolog et piloter un enregistrement LiveKit Egress.

## Configuration requise

- Dans Firebase Console, activer **Authentication > Sign-in method > Email/Password**.
- Configurer Firebase dans l'application Flutter avec FlutterFire et ajouter `firebase_auth`, `http` et le SDK LiveKit Flutter.
- L'API est disponible à `https://api.videolog.app`. LiveKit utilise l'URL retournée par l'API ; en production, elle doit être `wss://livekit.videolog.app`.
- Le backend doit avoir Firebase Admin configuré. Le compte de service reste exclusivement sur le serveur.

Imports Flutter utilisés dans les exemples :

```dart
import 'dart:convert';

import 'package:firebase_auth/firebase_auth.dart';
import 'package:http/http.dart' as http;
import 'package:livekit_client/livekit_client.dart';
```

## 1. Connexion email / mot de passe

Le client transmet les identifiants directement à Firebase Authentication. Il ne les envoie jamais à l'API Videolog.

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

Pour chaque appel API, obtenir l'ID token Firebase courant et le transmettre comme `Authorization: Bearer <idToken>`. Firebase renouvelle les tokens ; ne pas conserver un token expiré comme credential permanent.

```dart
Future<String> requireFirebaseIdToken() async {
  final user = FirebaseAuth.instance.currentUser;
  if (user == null) {
    throw StateError('Utilisateur Firebase non connecté');
  }
  final token = await user.getIdToken();
  if (token == null) {
    throw StateError('Impossible de récupérer le Firebase ID token');
  }
  return token;
}
```

## 2. Sélection d'une room

L'application peut récupérer ses rooms autorisées avec `GET /mobile/rooms`. La route exige un ID token Firebase et ne renvoie que les sessions non terminées dont l'utilisateur est propriétaire, participant, opérateur d'enregistrement, ou administrateur. Les rooms simplement accessibles par un custom claim `livekit_room_names` sont également listées, mais `can_control_recording` indique si l'utilisateur peut piloter Egress.

```http
GET https://api.videolog.app/mobile/rooms
Authorization: Bearer <Firebase ID token>
```

Le paramètre facultatif `limit` accepte une valeur de 1 à 100 et vaut 50 par défaut. Réponse :

```json
{
  "rooms": [
    {
      "room_name": "session-2026-10-25-a",
      "count": 2,
      "recording": false,
      "recording_status": "not_started",
      "created_at": "2026-10-25T10:00:00+00:00",
      "updated_at": "2026-10-25T10:05:00+00:00",
      "can_control_recording": true
    }
  ],
  "count": 1
}
```

Exemple Flutter pour charger la liste :

```dart
final idToken = await requireFirebaseIdToken();
final response = await http.get(
  Uri.parse('https://api.videolog.app/mobile/rooms'),
  headers: {'Authorization': 'Bearer $idToken'},
);

if (response.statusCode != 200) {
  throw Exception('Échec du chargement des rooms : HTTP ${response.statusCode}');
}

final body = jsonDecode(response.body) as Map<String, dynamic>;
final availableRooms = body['rooms'] as List<dynamic>;
```

Une room doit déjà exister dans les données de session. Le nom de room ne confère aucun droit à lui seul : le serveur vérifie l'UID Firebase pour l'accès LiveKit et l'autorisation opérateur pour contrôler l'enregistrement.

## 3. Obtenir un token LiveKit et rejoindre

`POST /token` accepte le nom de room ; l'API valide l'autorisation et signe le token LiveKit. Le client ne contient jamais les clés LiveKit serveur.

```dart
final idToken = await requireFirebaseIdToken();
final response = await http.post(
  Uri.parse('https://api.videolog.app/token'),
  headers: {
    'Content-Type': 'application/json',
    'Authorization': 'Bearer $idToken',
  },
  body: jsonEncode({'room': roomName}),
);

if (response.statusCode != 200) {
  throw Exception('Échec du token LiveKit : HTTP ${response.statusCode}');
}

final result = jsonDecode(response.body) as Map<String, dynamic>;
final liveKitToken = result['token'] as String;
final liveKitUrl = result['url'] as String;

final room = Room();
await room.connect(liveKitUrl, liveKitToken);
```

L'UID Firebase doit figurer dans `participant_uids`, `recording_operator_uids`, correspondre à `owner_uid`, ou disposer d'un droit d'administration serveur approprié pour accéder à la room.

## 4. Démarrer l'enregistrement

Le démarrage est manuel. Le minimum de deux participants ne s'applique pas à ce parcours ; il concerne le déclenchement automatique. La room doit toutefois être active et les utilisateurs doivent être connectés pour capturer la session.

```dart
final idToken = await requireFirebaseIdToken();
final roomPath = Uri.encodeComponent(roomName);
final response = await http.post(
  Uri.parse(
    'https://api.videolog.app/mobile/rooms/$roomPath/recording/start?layout=grid',
  ),
  headers: {'Authorization': 'Bearer $idToken'},
);

if (response.statusCode != 200) {
  throw Exception('Échec du démarrage Egress : HTTP ${response.statusCode}');
}
```

Layouts acceptés : `grid`, `speaker`, `single-speaker`. Le démarrage et l'arrêt exigent que l'utilisateur soit propriétaire de la session, figure dans `recording_operator_uids`, ou possède le custom claim d'administration autorisé côté serveur.

## 5. Suivre et arrêter

Lire l'état avec `GET /mobile/rooms/{room_name}/recording`. Le client peut rafraîchir l'état pendant que le statut indique un démarrage ou un arrêt en cours.

```dart
final idToken = await requireFirebaseIdToken();
final roomPath = Uri.encodeComponent(roomName);
final statusResponse = await http.get(
  Uri.parse('https://api.videolog.app/mobile/rooms/$roomPath/recording'),
  headers: {'Authorization': 'Bearer $idToken'},
);
```

Arrêter avec `POST /mobile/rooms/{room_name}/recording/stop` :

```dart
final idToken = await requireFirebaseIdToken();
final roomPath = Uri.encodeComponent(roomName);
final stopResponse = await http.post(
  Uri.parse(
    'https://api.videolog.app/mobile/rooms/$roomPath/recording/stop',
  ),
  headers: {'Authorization': 'Bearer $idToken'},
);
```

L'arrêt peut être demandé une seule fois ; un démarrage ou un arrêt déjà en cours peut répondre `409`. La durée maximale actuelle est de **180 secondes** : le serveur demande l'arrêt à l'échéance, même si le mobile ne l'a pas demandé.

## Sécurité

- Ne jamais envoyer le mot de passe à l'API Videolog.
- Ne jamais embarquer de secret Firebase Admin, clé LiveKit, secret MinIO ou identifiant Basic dans l'application.
- Ne jamais journaliser le mot de passe, l'ID token Firebase ou le JWT LiveKit.
- Ne pas faire confiance à un nom de room fourni par l'utilisateur sans les contrôles d'autorisation du backend.
- Utiliser `FirebaseAuth.instance.signOut()` pour fermer la session utilisateur.

Pour la configuration serveur, les structures de réponse et les détails de l'API, voir le [guide d'intégration mobile](./MOBILE_CLIENT_GUIDE.md).
