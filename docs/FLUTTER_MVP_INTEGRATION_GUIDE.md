# Guide d'adaptation de l'application Flutter — Videolog MVP (Phases 1 à 3)

Ce guide complète [MOBILE_CLIENT_GUIDE.md](./MOBILE_CLIENT_GUIDE.md) avec toutes les évolutions nécessaires dans l'application Flutter pour tirer parti du MVP Videolog (activités, métriques d'endurance, calendrier, séances structurées, PMC et forme du jour).

---

## 1. Prérequis : `ApiClient` & Contexte Organisation

Toutes les nouvelles routes sont sous `/v1/...` et utilisent le **Firebase ID token** en `Authorization: Bearer <id_token>`.

1. Au démarrage (après `FirebaseAuth.instance.currentUser`), appeler `GET /v1/me` :
   - Récupérer `user.uid`, `user.health_consent?.granted`, et `memberships`.
   - Conserver l'`org_id` actif (`memberships.first.org_id` par défaut, ou sélecteur si l'utilisateur a plusieurs organisations).
   - Conserver le `role` (`owner | admin | coach | athlete`).

```dart
import 'dart:convert';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:http/http.dart' as http;

class VideologApiClient {
  VideologApiClient({required this.baseUrl});
  final String baseUrl; // ex: https://api.videolog.app ou http://10.0.2.2:8001

  Future<Map<String, String>> _authHeaders({bool json = true}) async {
    final user = FirebaseAuth.instance.currentUser;
    if (user == null) throw StateError('Utilisateur non connecté');
    final token = await user.getIdToken();
    return {
      if (json) 'Content-Type': 'application/json',
      'Authorization': 'Bearer $token',
    };
  }

  Future<dynamic> get(String path) async {
    final res = await http.get(
      Uri.parse('$baseUrl$path'),
      headers: await _authHeaders(json: false),
    );
    if (res.statusCode < 200 || res.statusCode >= 300) {
      throw Exception('HTTP ${res.statusCode}: ${res.body}');
    }
    return jsonDecode(res.body);
  }

  Future<dynamic> post(String path, Map<String, dynamic> body) async {
    final res = await http.post(
      Uri.parse('$baseUrl$path'),
      headers: await _authHeaders(),
      body: jsonEncode(body),
    );
    if (res.statusCode < 200 || res.statusCode >= 300) {
      throw Exception('HTTP ${res.statusCode}: ${res.body}');
    }
    return jsonDecode(res.body);
  }

  Future<dynamic> patch(String path, Map<String, dynamic> body) async {
    final res = await http.patch(
      Uri.parse('$baseUrl$path'),
      headers: await _authHeaders(),
      body: jsonEncode(body),
    );
    if (res.statusCode < 200 || res.statusCode >= 300) {
      throw Exception('HTTP ${res.statusCode}: ${res.body}');
    }
    return jsonDecode(res.body);
  }
}
```

---

## 2. Mise à jour de la Télémétrie Live : Paquet Binaire v3 (25 octets)

Le serveur ([telemetry-agent/agent.py](../telemetry-agent/agent.py)) accepte toujours la v2 (21 octets), mais la **v3 (25 octets, big-endian)** ajoute la **puissance (`power_w`)** et corrige le `bearing` sur `uint16` (`0..359`).

| Offset | Taille | Type | Champ | Valeur nulle |
|---:|---:|---|---|---|
| 0 | 1 | `uint8` | version | `3` |
| 1 | 1 | `uint8` | rôle | `0=coach`, `1=trainee`, `2=observer` |
| 2 | 2 | `uint16` | vitesse (`km/h × 100`) | `65535` |
| 4 | 1 | `uint8` | FC (`bpm`) | `255` |
| 5 | 1 | `uint8` | cadence (`rpm`) | `255` |
| 6 | 4 | `int32` | latitude (`deg × 10^7`) | `0x7FFFFFFF` |
| 10 | 4 | `int32` | longitude (`deg × 10^7`) | `0x7FFFFFFF` |
| 14 | 2 | `int16` | altitude (`m × 100`) | `0x7FFF` |
| 16 | 2 | `uint16` | bearing (`0..359°`) | `65535` |
| 18 | 2 | `uint16` | puissance (`W`) | `65535` |
| 20 | 4 | `uint32` | timestamp (`Unix ms mod 2^32`) | — |
| 24 | 1 | `uint8` | flags | `0` |

```dart
import 'dart:typed_data';

Uint8List encodeTelemetryV3({
  required int roleCode,
  double? speedKmh,
  int? heartRate,
  int? cadence,
  double? lat,
  double? lng,
  double? altitudeM,
  int? bearingDeg,
  int? powerWatts,
  required int timestampMs,
}) {
  final bd = ByteData(25);
  bd.setUint8(0, 3);
  bd.setUint8(1, roleCode);
  bd.setUint16(2, speedKmh == null ? 65535 : (speedKmh * 100).round().clamp(0, 65534), Endian.big);
  bd.setUint8(4, heartRate == null ? 255 : heartRate.clamp(0, 254));
  bd.setUint8(5, cadence == null ? 255 : cadence.clamp(0, 254));
  bd.setInt32(6, lat == null ? 0x7FFFFFFF : (lat * 10000000).round(), Endian.big);
  bd.setInt32(10, lng == null ? 0x7FFFFFFF : (lng * 10000000).round(), Endian.big);
  bd.setInt16(14, altitudeM == null ? 0x7FFF : (altitudeM * 100).round().clamp(-32768, 32766), Endian.big);
  bd.setUint16(16, bearingDeg == null ? 65535 : bearingDeg.clamp(0, 359), Endian.big);
  bd.setUint16(18, powerWatts == null ? 65535 : powerWatts.clamp(0, 65534), Endian.big);
  bd.setUint32(20, timestampMs & 0xFFFFFFFF, Endian.big);
  bd.setUint8(24, 0);
  return bd.buffer.asUint8List();
}
```

---

## 3. Nouveaux écrans à ajouter dans Flutter

### Écran A : Accueil Athlète — Charge (PMC) & Forme du jour
1. **KPIs Fitness / Fatigue / Fraîcheur** :
   - `GET /v1/orgs/{org_id}/analytics/pmc?uid={uid}&from={today-30d}&include_projected=true`
   - Lire l'entrée du jour (`date == today`) : `ctl`, `atl`, `tsb`, `ramp_rate`.
   - Afficher les objectifs à venir (`goals[]` : `title`, `date`, `priority`).
2. **Questionnaire rapide « Forme du jour » (10 secondes)** :
   - `POST /v1/orgs/{org_id}/wellness?uid={uid}`
   - Corps JSON :
     ```json
     {
       "date": "2026-11-16",
       "sleep_h": 7.5,
       "sleep_quality": 4,
       "fatigue": 2,
       "soreness": 2,
       "stress": 2,
       "mood": 4
     }
     ```
   - **Règle RGPD** : n'envoyer `hrv_ms`, `resting_hr` ou `weight_kg` **que si** `user.health_consent.granted == true` (sinon l'API renvoie `422`).

### Écran B : Calendrier & « Séance du jour »
1. **Récupérer la semaine courante** :
   - `GET /v1/orgs/{org_id}/calendar?from=YYYY-MM-DD&to=YYYY-MM-DD&uid={uid}`
   - Retourne `planned_workouts[]`, `activities[]`, `goals[]` et `weekly_totals[]`.
2. **Afficher les étapes structurées d'une séance** :
   - Chaque `planned_workout` contient `title`, `sport`, `estimated_duration_s`, `estimated_tss`, `status` (`planned | done | partial | missed`), `compliance` (`score`, `color`), `blocks[]` (prêt pour dessiner la barre d'intensité colorée) et `steps[]`.
3. **Exporter vers la montre / home trainer** :
   - `GET /v1/orgs/{org_id}/workouts/planned/{planned_id}/export?format=fit` (ou `zwo`, `erg`).
   - Partager le fichier téléchargé via `share_plus` vers Garmin Connect / Zwift / Wahoo.

### Écran C : Activités, Import FIT/GPX/TCX & Débriefing
1. **Liste des activités** :
   - `GET /v1/orgs/{org_id}/activities?uid={uid}&limit=30`
2. **Importer un fichier depuis le téléphone** (`file_picker`) :
   ```dart
   Future<Map<String, dynamic>> uploadActivityFile({
     required String baseUrl,
     required String orgId,
     required String filePath,
     String? title,
   }) async {
     final token = await FirebaseAuth.instance.currentUser!.getIdToken();
     final req = http.MultipartRequest(
       'POST',
       Uri.parse('$baseUrl/v1/orgs/$orgId/activities/upload'),
     );
     req.headers['Authorization'] = 'Bearer $token';
     req.files.add(await http.MultipartFile.fromPath('file', filePath));
     if (title != null && title.isNotEmpty) {
       req.fields['title'] = title;
     }
     final streamed = await req.send();
     final body = await streamed.stream.bytesToString();
     if (streamed.statusCode != 201) {
       throw Exception('Import échoué (${streamed.statusCode}): $body');
     }
     return jsonDecode(body) as Map<String, dynamic>;
   }
   ```
   *(L'API détecte automatiquement les doublons en `409` et apparie la séance au calendrier si elle correspond.)*
3. **Détail d'une activité, RPE & Commentaires** :
   - Détail : `GET /v1/orgs/{org_id}/activities/{id}` (métriques `tss`, `normalized_power`, `intensity_factor`, `avg_hr`, `elevation_gain_m`, `laps[]`, `video_assets[]`).
   - Flux allégés pour graphique mobile (`fl_chart`) : `GET /v1/orgs/{org_id}/activities/{id}/streams?downsample=5`.
   - Saisie RPE / ressenti post-séance : `PATCH /v1/orgs/{org_id}/activities/{id}` avec `{"rpe": 7, "feeling": 4}`.
   - Commentaires coach/athlète : `GET` et `POST /v1/orgs/{org_id}/activities/{id}/comments` avec `{"body": "..."}`.

### Écran D : Seuils & Zones (Profil)
- Lire les seuils actifs : `GET /v1/me/thresholds`
- Mettre à jour un palier daté : `PUT /v1/orgs/{org_id}/athletes/{uid}/thresholds` avec :
  ```json
  {
    "sport": "cyclisme",
    "effective_from": "2026-11-16",
    "ftp_w": 265,
    "lthr_bpm": 172,
    "recalculate_activities": true
  }
  ```

---

## 4. Ordre de mise à jour recommandé côté Flutter

| Priorité | Lot | Effort estimé |
|---|---|---|
| **P0** | Passage à `encodeTelemetryV3` (25 octets) + lecture `org_id` depuis `/v1/me` | 0,5 jour |
| **P1** | Écran **Calendrier / Séance du jour** + export `.fit` + saisie **Forme du jour** | 2 à 3 jours |
| **P2** | Écran **Activités** (liste, détail KPIs, courbes `downsample=5`, RPE, commentaires, upload `.fit/.gpx`) | 3 à 4 jours |
| **P3** | Écran **Charge PMC** (`CTL/ATL/TSB`) + **Seuils & Zones** | 2 jours |

Côté UI, conservez la charte VideoLog (`#090B0E` fond, `#151A21` cartes, `#18ABF8` cyan, `#3DDC97` vert, `#E5373A` rouge).
