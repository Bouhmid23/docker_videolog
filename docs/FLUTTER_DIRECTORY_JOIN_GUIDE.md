# Guide d'intégration Flutter — Annuaire Clubs/Coachs & Demandes d'adhésion

Ce guide complète [FLUTTER_MVP_INTEGRATION_GUIDE.md](./FLUTTER_MVP_INTEGRATION_GUIDE.md) et [MOBILE_CLIENT_GUIDE.md](./MOBILE_CLIENT_GUIDE.md). Il détaille tout ce qu'il faut implémenter côté **application mobile Flutter** pour permettre à un utilisateur (y compris fraîchement inscrit sans organisation) de :

1. Charger le **référentiel complet des sports** (indoor/outdoor, catégories, métriques/capteurs supportés).
2. Explorer et filtrer les **clubs et coachs** avec **toutes les informations sur les sports qu'ils pratiquent et encadrent**.
3. Envoyer, suivre et annuler une **demande d'adhésion**.
4. Réagir automatiquement à l'approbation ou au refus côté club/coach.

---

## 1. Base URL, Authentification & Extension de `VideologApiClient`

Toutes les nouvelles routes sont exposées sur :
- **Production mobile** : `https://api.<domaine>/v1/...` *(exposé via `caddy/Caddyfile`)*
- **Alternative web/proxy** : `https://app.<domaine>/api/v1/...`
- **Dev local** : `http://10.0.2.2:8001/v1/...` (émulateur Android) ou `http://localhost:8001/v1/...` (simulateur iOS)

### Règles d'authentification par endpoint

| Endpoint | Auth requise | E-mail vérifié requis ? | Organisation requise ? |
|---|---|---|---|
| `GET /v1/sports` | Aucune (publique) | Non | Non |
| `GET /v1/directory/organizations` | Bearer Firebase ID Token | Non | **Non** (accessible dès l'inscription) |
| `GET /v1/directory/organizations/{org_id}` | Bearer Firebase ID Token | Non | **Non** |
| `POST /v1/orgs/{org_id}/join-requests` | Bearer Firebase ID Token | **Oui** (`email_verified == true`) | **Non** |
| `GET /v1/me/join-requests` | Bearer Firebase ID Token | Non | **Non** |
| `DELETE /v1/me/join-requests/{request_id}` | Bearer Firebase ID Token | Non | **Non** |

> **Important (Parcours nouvel inscrit)** : dans `GET /v1/me`, un nouvel athlète mobile a `needs_onboarding: true` et `memberships: []`. Sur mobile, **ne pas le bloquer** sur un écran d'onboarding club/coach : proposez-lui directement d'explorer l'annuaire pour demander à rejoindre un club ou un coach !

### Extension recommandée de `VideologApiClient`

Ajoutez une exception typée `ApiException` (pour afficher les messages `detail` renvoyés par FastAPI) ainsi que la méthode `delete` et le support des query parameters :

```dart
import 'dart:convert';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:http/http.dart' as http;

class ApiException implements Exception {
  ApiException(this.statusCode, this.detail);
  final int statusCode;
  final String detail;

  @override
  String toString() => 'ApiException($statusCode): $detail';
}

class VideologApiClient {
  VideologApiClient({required this.baseUrl});
  final String baseUrl;

  Future<Map<String, String>> _authHeaders({
    bool json = true,
    bool requireAuth = true,
  }) async {
    final headers = <String, String>{
      if (json) 'Content-Type': 'application/json',
    };
    if (requireAuth) {
      final user = FirebaseAuth.instance.currentUser;
      if (user == null) throw StateError('Utilisateur non connecté');
      final token = await user.getIdToken();
      headers['Authorization'] = 'Bearer $token';
    }
    return headers;
  }

  Uri _buildUri(String path, [Map<String, String>? query]) {
    final uri = Uri.parse('$baseUrl$path');
    if (query == null || query.isEmpty) return uri;
    return uri.replace(queryParameters: query);
  }

  dynamic _decodeOrThrow(http.Response res) {
    final decoded = res.body.isNotEmpty ? jsonDecode(res.body) : null;
    if (res.statusCode < 200 || res.statusCode >= 300) {
      String detail = 'Erreur serveur (${res.statusCode})';
      if (decoded is Map<String, dynamic> && decoded['detail'] != null) {
        final raw = decoded['detail'];
        if (raw is String) {
          detail = raw;
        } else if (raw is List && raw.isNotEmpty && raw.first is Map) {
          detail = (raw.first['msg'] ?? detail).toString();
        }
      }
      throw ApiException(res.statusCode, detail);
    }
    return decoded;
  }

  Future<dynamic> get(
    String path, {
    Map<String, String>? query,
    bool requireAuth = true,
  }) async {
    final res = await http.get(
      _buildUri(path, query),
      headers: await _authHeaders(json: false, requireAuth: requireAuth),
    );
    return _decodeOrThrow(res);
  }

  Future<dynamic> post(String path, Map<String, dynamic> body) async {
    final res = await http.post(
      _buildUri(path),
      headers: await _authHeaders(),
      body: jsonEncode(body),
    );
    return _decodeOrThrow(res);
  }

  Future<dynamic> delete(String path) async {
    final res = await http.delete(
      _buildUri(path),
      headers: await _authHeaders(json: false),
    );
    return _decodeOrThrow(res);
  }
}
```

---

## 2. Contrats d'API complets

### 2.1 Référentiel des sports — `GET /v1/sports`

Permet de peupler les filtres de recherche, les badges Indoor/Outdoor et les libellés sans hardcoder les 17 sports dans l'app.

**Query params optionnels :**
- `is_indoor`: `true` | `false`
- `category`: `ergo` | `strength` | `skill` | `outdoor`

**Exemple de réponse (`200 OK`) :**
```json
{
  "sports": [
    {
      "code": "home_trainer",
      "label": "Home-trainer / Cyclisme indoor",
      "is_indoor": true,
      "category": "ergo",
      "cadence_unit": "rpm",
      "cadence_max": 120,
      "supports_power": true,
      "supports_gps": false,
      "supports_reps": false,
      "supports_pace_km": false,
      "supports_pace_500m": false,
      "supports_css": false
    }
  ],
  "count": 17
}
```

---

### 2.2 Annuaire des clubs & coachs — `GET /v1/directory/organizations`

Retourne les clubs (`club`) et coachs privés (`solo_coach`) actifs et publics (`is_public != false`), avec **tous les détails de leurs sports** (fusion automatique de `org.sports`, des sports des groupes et des spécialités `coach_profile.sports` des entraîneurs).

**Query params optionnels :**

| Paramètre | Type | Description |
|---|---|---|
| `type` | `club` \| `solo_coach` | Filtrer par type de structure |
| `sport` | `string` | Code sport (ex : `home_trainer`, `cyclisme`, `aviron_indoor`, `cross_training`) |
| `indoor_only` | `bool` | Si `true`, ne retourne que les structures proposant au moins un sport indoor |
| `city` | `string` | Recherche partielle insensible à la casse sur la ville (ex : `lyon`) |
| `q` | `string` | Recherche libre sur le nom, la ville, la description, les sports, les noms/bios/certifications des coachs |
| `accepting_only` | `bool` | Si `true`, exclut les structures ayant fermé les demandes d'adhésion |
| `limit` | `int` | Taille de page (`1..100`, défaut `20`) |
| `offset` | `int` | Décalage pagination (défaut `0`) |

**Exemple de réponse (`200 OK`) :**
```json
{
  "organizations": [
    {
      "id": "org-uuid-1",
      "type": "club",
      "name": "VC Lyon Endurance",
      "description": "Club route & home-trainer connecté à Lyon.",
      "city": "Lyon",
      "country": "FR",
      "logo_url": null,
      "accepting_join_requests": true,
      "sports": ["cyclisme", "home_trainer"],
      "has_indoor_sports": true,
      "sports_details": [
        {
          "code": "home_trainer",
          "label": "Home-trainer / Cyclisme indoor",
          "is_indoor": true,
          "category": "ergo",
          "cadence_unit": "rpm",
          "cadence_max": 120,
          "supports_power": true,
          "supports_gps": false,
          "supports_reps": false,
          "supports_pace_km": false,
          "supports_pace_500m": false,
          "supports_css": false,
          "levels": ["compétition régionale", "élite"],
          "max_years_experience": 9,
          "groups": [
            {
              "id": "grp-1",
              "name": "Groupe Home-Trainer Hiver",
              "sport": "home_trainer",
              "coach_uids": ["coach-uid-1"]
            }
          ],
          "coaches": [
            {
              "uid": "coach-uid-1",
              "name": "Marc Martin",
              "level": "élite",
              "years_experience": 9
            }
          ]
        }
      ],
      "coaches": [
        {
          "uid": "coach-uid-1",
          "name": "Marc Martin",
          "role": "owner",
          "photo_url": null,
          "certifications": "DEJEPS Cyclisme",
          "bio": "Préparation puissance et contre-la-montre.",
          "sports": [
            {
              "code": "home_trainer",
              "label": "Home-trainer / Cyclisme indoor",
              "is_indoor": true,
              "category": "ergo",
              "level": "élite",
              "years_experience": 9
            }
          ]
        }
      ],
      "groups": [
        {
          "id": "grp-1",
          "name": "Groupe Home-Trainer Hiver",
          "sport": "home_trainer",
          "sport_label": "Home-trainer / Cyclisme indoor",
          "is_indoor": true,
          "category": "ergo",
          "coach_uids": ["coach-uid-1"]
        }
      ],
      "counts": {
        "athletes": 24,
        "coaches": 2,
        "groups": 3
      },
      "is_member": false,
      "my_role": null,
      "pending_request_id": null,
      "created_at": "2026-11-10T09:00:00+00:00"
    }
  ],
  "total": 1,
  "limit": 20,
  "offset": 0
}
```

---

### 2.3 Fiche détaillée d'un club ou coach — `GET /v1/directory/organizations/{org_id}`

Retourne la même structure `organization` complète + le champ `my_join_request` (la dernière demande de l'utilisateur pour cette structure, ou `null`).

```json
{
  "organization": { "...": "..." },
  "my_join_request": {
    "id": "req-uuid",
    "status": "pending",
    "requested_sports": ["home_trainer"],
    "requested_group_id": "grp-1",
    "target_coach_uid": "coach-uid-1",
    "message": "Bonjour, je cherche un suivi home-trainer.",
    "decision_note": null,
    "created_at": "2026-11-19T10:30:00+00:00",
    "decided_at": null
  }
}
```

---

### 2.4 Soumettre une demande d'adhésion — `POST /v1/orgs/{org_id}/join-requests`

**Body JSON :**
```json
{
  "requested_sports": ["home_trainer", "cyclisme"],
  "requested_group_id": "grp-1",
  "target_coach_uid": "coach-uid-1",
  "message": "Bonjour, je cherche un suivi structuré pour la saison hivernale.",
  "first_name": "Alice",
  "last_name": "Durand",
  "age_confirmed": true
}
```

> **Conseils payload :**
> - Tous les champs sont optionnels sauf `age_confirmed` (défaut `true`, mais doit être explicitement confirmé par l'utilisateur s'il n'a pas encore de `birth_date` sur son profil).
> - Envoyez `first_name` / `last_name` si `me.user?.first_name` est vide afin que le club identifie immédiatement le candidat.
> - Pour un `solo_coach`, `requested_group_id` et `target_coach_uid` peuvent être omis (le backend renseigne automatiquement `target_coach_uid = owner_uid`).

**Réponse (`201 Created`) :**
```json
{
  "join_request": {
    "id": "req-uuid",
    "org_id": "org-uuid-1",
    "uid": "firebase-uid",
    "org_name": "VC Lyon Endurance",
    "org_type": "club",
    "requested_sports": ["home_trainer", "cyclisme"],
    "requested_sports_details": [ { "code": "home_trainer", "label": "Home-trainer / Cyclisme indoor", "...": "..." } ],
    "requested_group_id": "grp-1",
    "target_coach_uid": "coach-uid-1",
    "message": "Bonjour, je cherche un suivi structuré pour la saison hivernale.",
    "status": "pending",
    "created_at": "2026-11-19T10:30:00+00:00"
  }
}
```

---

### 2.5 Lister mes demandes — `GET /v1/me/join-requests`

Query param optionnel : `?status=pending|approved|rejected|cancelled`.

**Réponse (`200 OK`) :**
```json
{
  "join_requests": [
    {
      "id": "req-uuid",
      "org_id": "org-uuid-1",
      "org_name": "VC Lyon Endurance",
      "org_type": "club",
      "org_city": "Lyon",
      "org_sports_details": [ { "code": "cyclisme", "label": "Cyclisme (route / VTT)", "...": "..." } ],
      "requested_sports": ["home_trainer"],
      "requested_sports_details": [ { "code": "home_trainer", "label": "Home-trainer / Cyclisme indoor", "...": "..." } ],
      "requested_group_id": "grp-1",
      "requested_group_name": "Groupe Home-Trainer Hiver",
      "target_coach_uid": "coach-uid-1",
      "target_coach_name": "Marc Martin",
      "message": "Bonjour...",
      "status": "approved",
      "decision_note": "Bienvenue au VC Lyon !",
      "created_at": "2026-11-19T10:30:00+00:00",
      "decided_at": "2026-11-19T11:00:00+00:00"
    }
  ],
  "count": 1
}
```

---

### 2.6 Annuler une demande en attente — `DELETE /v1/me/join-requests/{request_id}`

**Réponse (`200 OK`) :**
```json
{
  "status": "cancelled",
  "id": "req-uuid"
}
```

---

## 3. Modèles Dart prêts à l'emploi

Créez `lib/models/directory_models.dart` :

```dart
class SportMeta {
  const SportMeta({
    required this.code,
    required this.label,
    required this.isIndoor,
    required this.category,
    required this.cadenceUnit,
    required this.cadenceMax,
    required this.supportsPower,
    required this.supportsGps,
    required this.supportsReps,
    required this.supportsPaceKm,
    required this.supportsPace500m,
    required this.supportsCss,
  });

  final String code;
  final String label;
  final bool isIndoor;
  final String category; // ergo | strength | skill | outdoor
  final String cadenceUnit; // rpm | spm | reps/min
  final int cadenceMax;
  final bool supportsPower;
  final bool supportsGps;
  final bool supportsReps;
  final bool supportsPaceKm;
  final bool supportsPace500m;
  final bool supportsCss;

  factory SportMeta.fromJson(Map<String, dynamic> j) => SportMeta(
        code: j['code'] as String,
        label: j['label'] as String,
        isIndoor: j['is_indoor'] as bool? ?? false,
        category: j['category'] as String? ?? 'outdoor',
        cadenceUnit: j['cadence_unit'] as String? ?? 'rpm',
        cadenceMax: (j['cadence_max'] as num?)?.toInt() ?? 120,
        supportsPower: j['supports_power'] as bool? ?? false,
        supportsGps: j['supports_gps'] as bool? ?? false,
        supportsReps: j['supports_reps'] as bool? ?? false,
        supportsPaceKm: j['supports_pace_km'] as bool? ?? false,
        supportsPace500m: j['supports_pace_500m'] as bool? ?? false,
        supportsCss: j['supports_css'] as bool? ?? false,
      );
}

class SportCoachSummary {
  const SportCoachSummary({
    required this.uid,
    required this.name,
    this.level,
    this.yearsExperience,
  });

  final String uid;
  final String name;
  final String? level;
  final int? yearsExperience;

  factory SportCoachSummary.fromJson(Map<String, dynamic> j) =>
      SportCoachSummary(
        uid: j['uid'] as String,
        name: j['name'] as String,
        level: j['level'] as String?,
        yearsExperience: (j['years_experience'] as num?)?.toInt(),
      );
}

class DirectoryGroup {
  const DirectoryGroup({
    required this.id,
    required this.name,
    required this.sport,
    this.sportLabel,
    this.isIndoor = false,
    this.coachUids = const [],
  });

  final String id;
  final String name;
  final String sport;
  final String? sportLabel;
  final bool isIndoor;
  final List<String> coachUids;

  factory DirectoryGroup.fromJson(Map<String, dynamic> j) => DirectoryGroup(
        id: j['id'] as String,
        name: j['name'] as String,
        sport: j['sport'] as String,
        sportLabel: j['sport_label'] as String?,
        isIndoor: j['is_indoor'] as bool? ?? false,
        coachUids: List<String>.from(j['coach_uids'] as List? ?? const []),
      );
}

class OrgSportDetail extends SportMeta {
  const OrgSportDetail({
    required super.code,
    required super.label,
    required super.isIndoor,
    required super.category,
    required super.cadenceUnit,
    required super.cadenceMax,
    required super.supportsPower,
    required super.supportsGps,
    required super.supportsReps,
    required super.supportsPaceKm,
    required super.supportsPace500m,
    required super.supportsCss,
    required this.levels,
    this.maxYearsExperience,
    required this.groups,
    required this.coaches,
  });

  final List<String> levels;
  final int? maxYearsExperience;
  final List<DirectoryGroup> groups;
  final List<SportCoachSummary> coaches;

  factory OrgSportDetail.fromJson(Map<String, dynamic> j) {
    final base = SportMeta.fromJson(j);
    return OrgSportDetail(
      code: base.code,
      label: base.label,
      isIndoor: base.isIndoor,
      category: base.category,
      cadenceUnit: base.cadenceUnit,
      cadenceMax: base.cadenceMax,
      supportsPower: base.supportsPower,
      supportsGps: base.supportsGps,
      supportsReps: base.supportsReps,
      supportsPaceKm: base.supportsPaceKm,
      supportsPace500m: base.supportsPace500m,
      supportsCss: base.supportsCss,
      levels: List<String>.from(j['levels'] as List? ?? const []),
      maxYearsExperience: (j['max_years_experience'] as num?)?.toInt(),
      groups: (j['groups'] as List? ?? const [])
          .map((e) => DirectoryGroup.fromJson(e as Map<String, dynamic>))
          .toList(),
      coaches: (j['coaches'] as List? ?? const [])
          .map((e) => SportCoachSummary.fromJson(e as Map<String, dynamic>))
          .toList(),
    );
  }
}

class DirectoryCoachSport {
  const DirectoryCoachSport({
    required this.code,
    required this.label,
    required this.isIndoor,
    required this.level,
    this.yearsExperience,
  });

  final String code;
  final String label;
  final bool isIndoor;
  final String level;
  final int? yearsExperience;

  factory DirectoryCoachSport.fromJson(Map<String, dynamic> j) =>
      DirectoryCoachSport(
        code: j['code'] as String,
        label: j['label'] as String,
        isIndoor: j['is_indoor'] as bool? ?? false,
        level: j['level'] as String? ?? 'loisir',
        yearsExperience: (j['years_experience'] as num?)?.toInt(),
      );
}

class DirectoryCoach {
  const DirectoryCoach({
    required this.uid,
    required this.name,
    required this.role,
    this.photoUrl,
    this.certifications,
    this.bio,
    required this.sports,
  });

  final String uid;
  final String name;
  final String role;
  final String? photoUrl;
  final String? certifications;
  final String? bio;
  final List<DirectoryCoachSport> sports;

  factory DirectoryCoach.fromJson(Map<String, dynamic> j) => DirectoryCoach(
        uid: j['uid'] as String,
        name: j['name'] as String,
        role: j['role'] as String? ?? 'coach',
        photoUrl: j['photo_url'] as String?,
        certifications: j['certifications'] as String?,
        bio: j['bio'] as String?,
        sports: (j['sports'] as List? ?? const [])
            .map((e) => DirectoryCoachSport.fromJson(e as Map<String, dynamic>))
            .toList(),
      );
}

class DirectoryOrganization {
  const DirectoryOrganization({
    required this.id,
    required this.type,
    required this.name,
    this.description,
    this.city,
    required this.country,
    this.logoUrl,
    required this.acceptingJoinRequests,
    required this.sports,
    required this.sportsDetails,
    required this.hasIndoorSports,
    required this.coaches,
    required this.groups,
    required this.athletesCount,
    required this.coachesCount,
    required this.groupsCount,
    required this.isMember,
    this.myRole,
    this.pendingRequestId,
  });

  final String id;
  final String type; // club | solo_coach
  final String name;
  final String? description;
  final String? city;
  final String country;
  final String? logoUrl;
  final bool acceptingJoinRequests;
  final List<String> sports;
  final List<OrgSportDetail> sportsDetails;
  final bool hasIndoorSports;
  final List<DirectoryCoach> coaches;
  final List<DirectoryGroup> groups;
  final int athletesCount;
  final int coachesCount;
  final int groupsCount;
  final bool isMember;
  final String? myRole;
  final String? pendingRequestId;

  bool get isSoloCoach => type == 'solo_coach';

  factory DirectoryOrganization.fromJson(Map<String, dynamic> j) {
    final counts = (j['counts'] as Map<String, dynamic>?) ?? const {};
    return DirectoryOrganization(
      id: j['id'] as String,
      type: j['type'] as String? ?? 'club',
      name: j['name'] as String? ?? '',
      description: j['description'] as String?,
      city: j['city'] as String?,
      country: j['country'] as String? ?? 'FR',
      logoUrl: j['logo_url'] as String?,
      acceptingJoinRequests: j['accepting_join_requests'] as bool? ?? true,
      sports: List<String>.from(j['sports'] as List? ?? const []),
      sportsDetails: (j['sports_details'] as List? ?? const [])
          .map((e) => OrgSportDetail.fromJson(e as Map<String, dynamic>))
          .toList(),
      hasIndoorSports: j['has_indoor_sports'] as bool? ?? false,
      coaches: (j['coaches'] as List? ?? const [])
          .map((e) => DirectoryCoach.fromJson(e as Map<String, dynamic>))
          .toList(),
      groups: (j['groups'] as List? ?? const [])
          .map((e) => DirectoryGroup.fromJson(e as Map<String, dynamic>))
          .toList(),
      athletesCount: (counts['athletes'] as num?)?.toInt() ?? 0,
      coachesCount: (counts['coaches'] as num?)?.toInt() ?? 0,
      groupsCount: (counts['groups'] as num?)?.toInt() ?? 0,
      isMember: j['is_member'] as bool? ?? false,
      myRole: j['my_role'] as String?,
      pendingRequestId: j['pending_request_id'] as String?,
    );
  }
}

class MyJoinRequest {
  const MyJoinRequest({
    required this.id,
    required this.orgId,
    required this.orgName,
    required this.orgType,
    this.orgCity,
    required this.requestedSports,
    this.requestedGroupId,
    this.requestedGroupName,
    this.targetCoachUid,
    this.targetCoachName,
    this.message,
    required this.status,
    this.decisionNote,
    this.createdAt,
    this.decidedAt,
  });

  final String id;
  final String orgId;
  final String orgName;
  final String orgType;
  final String? orgCity;
  final List<String> requestedSports;
  final String? requestedGroupId;
  final String? requestedGroupName;
  final String? targetCoachUid;
  final String? targetCoachName;
  final String? message;
  final String status; // pending | approved | rejected | cancelled
  final String? decisionNote;
  final DateTime? createdAt;
  final DateTime? decidedAt;

  factory MyJoinRequest.fromJson(Map<String, dynamic> j) => MyJoinRequest(
        id: j['id'] as String,
        orgId: j['org_id'] as String,
        orgName: j['org_name'] as String? ?? 'Organisation',
        orgType: j['org_type'] as String? ?? 'club',
        orgCity: j['org_city'] as String?,
        requestedSports:
            List<String>.from(j['requested_sports'] as List? ?? const []),
        requestedGroupId: j['requested_group_id'] as String?,
        requestedGroupName: j['requested_group_name'] as String?,
        targetCoachUid: j['target_coach_uid'] as String?,
        targetCoachName: j['target_coach_name'] as String?,
        message: j['message'] as String?,
        status: j['status'] as String? ?? 'pending',
        decisionNote: j['decision_note'] as String?,
        createdAt: j['created_at'] != null
            ? DateTime.tryParse(j['created_at'] as String)
            : null,
        decidedAt: j['decided_at'] != null
            ? DateTime.tryParse(j['decided_at'] as String)
            : null,
      );
}
```

---

## 4. Repository Flutter (`DirectoryRepository`)

Créez `lib/services/directory_repository.dart` :

```dart
import '../models/directory_models.dart';
import 'videolog_api_client.dart';

class DirectoryPageResult {
  const DirectoryPageResult({
    required this.organizations,
    required this.total,
    required this.limit,
    required this.offset,
  });
  final List<DirectoryOrganization> organizations;
  final int total;
  final int limit;
  final int offset;

  bool get hasMore => offset + organizations.length < total;
}

class DirectoryRepository {
  DirectoryRepository(this._api);
  final VideologApiClient _api;

  Future<List<SportMeta>> fetchSports({
    bool? isIndoor,
    String? category,
  }) async {
    final query = <String, String>{
      if (isIndoor != null) 'is_indoor': isIndoor.toString(),
      if (category != null && category.isNotEmpty) 'category': category,
    };
    final data = await _api.get('/v1/sports', query: query, requireAuth: false)
        as Map<String, dynamic>;
    return (data['sports'] as List)
        .map((e) => SportMeta.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<DirectoryPageResult> searchOrganizations({
    String? type, // 'club' | 'solo_coach'
    String? sport,
    bool indoorOnly = false,
    String? city,
    String? q,
    bool acceptingOnly = false,
    int limit = 20,
    int offset = 0,
  }) async {
    final query = <String, String>{
      'limit': '$limit',
      'offset': '$offset',
      if (type != null && type.isNotEmpty) 'type': type,
      if (sport != null && sport.isNotEmpty) 'sport': sport,
      if (indoorOnly) 'indoor_only': 'true',
      if (city != null && city.trim().isNotEmpty) 'city': city.trim(),
      if (q != null && q.trim().isNotEmpty) 'q': q.trim(),
      if (acceptingOnly) 'accepting_only': 'true',
    };
    final data = await _api.get('/v1/directory/organizations', query: query)
        as Map<String, dynamic>;
    final items = (data['organizations'] as List)
        .map((e) => DirectoryOrganization.fromJson(e as Map<String, dynamic>))
        .toList();
    return DirectoryPageResult(
      organizations: items,
      total: (data['total'] as num?)?.toInt() ?? items.length,
      limit: (data['limit'] as num?)?.toInt() ?? limit,
      offset: (data['offset'] as num?)?.toInt() ?? offset,
    );
  }

  Future<DirectoryOrganization> fetchOrganizationDetail(String orgId) async {
    final data = await _api.get('/v1/directory/organizations/$orgId')
        as Map<String, dynamic>;
    return DirectoryOrganization.fromJson(
      data['organization'] as Map<String, dynamic>,
    );
  }

  Future<String> submitJoinRequest({
    required String orgId,
    List<String> requestedSports = const [],
    String? requestedGroupId,
    String? targetCoachUid,
    String? message,
    String? firstName,
    String? lastName,
    bool ageConfirmed = true,
  }) async {
    final body = <String, dynamic>{
      'requested_sports': requestedSports,
      'age_confirmed': ageConfirmed,
      if (requestedGroupId != null) 'requested_group_id': requestedGroupId,
      if (targetCoachUid != null) 'target_coach_uid': targetCoachUid,
      if (message != null && message.trim().isNotEmpty)
        'message': message.trim(),
      if (firstName != null && firstName.trim().isNotEmpty)
        'first_name': firstName.trim(),
      if (lastName != null && lastName.trim().isNotEmpty)
        'last_name': lastName.trim(),
    };
    final data = await _api.post('/v1/orgs/$orgId/join-requests', body)
        as Map<String, dynamic>;
    final req = data['join_request'] as Map<String, dynamic>;
    return req['id'] as String;
  }

  Future<List<MyJoinRequest>> fetchMyJoinRequests({String? status}) async {
    final query = <String, String>{
      if (status != null && status.isNotEmpty) 'status': status,
    };
    final data = await _api.get('/v1/me/join-requests', query: query)
        as Map<String, dynamic>;
    return (data['join_requests'] as List)
        .map((e) => MyJoinRequest.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<void> cancelJoinRequest(String requestId) async {
    await _api.delete('/v1/me/join-requests/$requestId');
  }
}
```

---

## 5. Parcours UI recommandé dans Flutter

```mermaid
flowchart TD
    A["Écran Explorer (Annuaire)"] -->|Clic carte| B["Fiche Club / Coach"]
    A -->|Filtres: Type / Sport / Indoor / Ville / Recherche| A
    B -->|Bouton 'Demander l'adhésion'| C["BottomSheet Demande"]
    C -->|POST /v1/orgs/{id}/join-requests| D["État: Demande en attente"]
    D -->|Bouton 'Annuler'| E["DELETE /v1/me/join-requests/{id}"]
    A -->|Onglet 'Mes demandes'| F["Écran Mes demandes"]
    F -->|Notification join_request_approved| G["Rafraîchir GET /v1/me -> Basculer sur l'org"]
```

### Écran 1 : Explorer Clubs & Coachs
- **Barre de recherche** (`q` avec debounce `350 ms`).
- **Chips de filtre rapide** :
  - Segmented control : `Tous` | `Clubs` (`club`) | `Coachs` (`solo_coach`).
  - Switch / Chip : `Sports indoor uniquement` (`indoor_only=true`).
  - Sélecteur de sport alimenté par `GET /v1/sports` (regroupé par `isIndoor` ou `category`).
  - Champ optionnel `Ville`.
- **Carte de résultat** :
  - Nom + badge `Club` / `Coach privé` + ville.
  - Résumé `counts` (`24 athlètes · 2 coachs`).
  - Ligne de **chips sports** issus de `org.sportsDetails` avec icône/tag `Indoor` si `sport.isIndoor == true`.
  - Badge d'état à droite :
    - `isMember == true` → Badge vert **« Membre »**
    - `pendingRequestId != null` → Badge bleu **« En attente »**
    - `acceptingJoinRequests == false` → Badge gris **« Complet / Fermé »**

### Écran 2 : Fiche détaillée Club / Coach (`org_id`)
1. **En-tête** : Nom, type (`Club` / `Coach privé`), ville, `description`.
2. **Section « Sports pratiqués & encadrés »** *(boucle sur `org.sportsDetails`)* :
   - Carte par discipline affichant :
     - `sport.label` + badge `Indoor` / `Outdoor` + `category`.
     - **Métriques / Capteurs pris en charge** (badges conditionnels) :
       - `supportsPower` → `Puissance (W)`
       - `supportsPaceKm` → `Allure /km`
       - `supportsPace500m` → `Allure /500m`
       - `supportsCss` → `CSS /100m`
       - `supportsReps` → `Répétitions`
       - `supportsGps` → `GPS`
       - `cadenceUnit` → `Cadence (${sport.cadenceUnit})`
     - **Niveaux encadrés** (`sport.levels`, ex : `Compétition régionale`, `Élite`) et **expérience** (`Jusqu'à ${sport.maxYearsExperience} ans d'expérience`).
     - **Groupes associés** (`sport.groups`) et **coachs spécialisés** (`sport.coaches`).
3. **Section « Équipe d'encadrement »** *(boucle sur `org.coaches`)* :
   - Nom, `certifications`, `bio`, et liste de leurs sports avec niveau & années d'expérience.
4. **Bouton d'action en bas (CTA collant)** :
   - Si `org.isMember` → Bouton secondaire **« Ouvrir cet espace »** (bascule l'`org_id` actif).
   - Sinon si `org.pendingRequestId != null` → Bouton **« Annuler ma demande »** (avec dialogue de confirmation → `cancelJoinRequest`).
   - Sinon si `!org.acceptingJoinRequests` → Bouton désactivé **« Adhésions fermées »**.
   - Sinon → Bouton principal **« Demander l'adhésion »** (ouvre le BottomSheet).

### Écran 3 : BottomSheet « Demander l'adhésion »
- **Sports souhaités** : sélection multiple parmi `org.sportsDetails` (pré-coché si l'org n'a qu'un sport ou si le profil athlète matche un sport de l'org).
- **Si `org.type == 'club'` et `org.groups.isNotEmpty`** : dropdown optionnel **Groupe souhaité** (filtrable selon les sports cochés).
- **Si `org.type == 'club'` et `org.coaches.length > 1`** : dropdown optionnel **Coach souhaité**.
- **Identité** : si `me.user.firstName` ou `lastName` est vide, afficher 2 champs `Prénom` / `Nom` requis.
- **Message de présentation** : `TextField` multiligne optionnel (max `1000` caractères, compteur affiché).
- **Case à cocher RGPD / Âge** : `Je confirme avoir au moins 15 ans` (obligatoire si non encore vérifié sur le profil).

### Écran 4 : « Mes demandes d'adhésion »
- Accessible depuis l'onglet Annuaire ou *Mon Profil*.
- Affiche la liste retournée par `GET /v1/me/join-requests` avec badges de statut :
  - `pending` → Bleu (*En attente*) + bouton **Annuler**
  - `approved` → Vert (*Acceptée*) + `decision_note` + bouton **Accéder au club/coach**
  - `rejected` → Rouge (*Refusée*) + `decision_note` + bouton **Voir la fiche** (permet de re-postuler ultérieurement)
  - `cancelled` → Gris (*Annulée*)

---

## 6. Gestion des erreurs HTTP & Notifications

### Tableau des erreurs à gérer proprement dans l'UI

| Code HTTP | Cause côté API | Comportement recommandé dans Flutter |
|---|---|---|
| `401` | Token Firebase expiré | Forcer `user.getIdToken(true)` et réessayer une fois |
| `403` | E-mail non vérifié (`Veuillez vérifier votre adresse e-mail...`) | Afficher un bandeau + bouton **« Renvoyer l'e-mail de vérification »** (`user.sendEmailVerification()`) |
| `403` | Club/coach fermé (`accepting_join_requests == false`) | Afficher `e.detail` en SnackBar et rafraîchir la fiche |
| `404` | Club/coach introuvable ou passé privé | Retour à l'annuaire + SnackBar |
| `409` | Déjà membre, demande déjà en attente, ou limite de 10 demandes `pending` atteinte | Afficher `e.detail` et rafraîchir l'état (`fetchMyJoinRequests` / `refreshMe`) |
| `422` | Moins de 15 ans, sport/groupe/coach invalide | Afficher `e.detail` sous le formulaire |
| `429` | Rate limit dépassé (10 demandes/heure) | Afficher un message invitant à réessayer plus tard |

### Synchronisation lors de l'acceptation (Notifications)

L'app mobile peut poller `GET /v1/me/notifications` (ou à la reprise d'app `AppLifecycleState.resumed`) :
- Sur une notification de type **`join_request_approved`** :
  1. Appeler immédiatement `GET /v1/me` pour recharger `memberships`.
  2. Proposer (ou basculer automatiquement si c'était la première org) de sélectionner `notif.org_id` comme organisation active.
  3. Invalider le cache de l'annuaire et de *Mes demandes*.
- Sur une notification de type **`join_request_rejected`** :
  1. Afficher le message (`notif.body` contient la `decision_note`) et rafraîchir *Mes demandes*.

---

## 7. Checklist de validation QA Mobile

- [ ] Un utilisateur **sans aucune organisation** (`memberships: []`) peut ouvrir l'annuaire (`GET /v1/directory/organizations`) et consulter les fiches détaillées.
- [ ] Les filtres `type=club`, `type=solo_coach`, `sport=home_trainer`, `indoor_only=true`, `city` et `q` retournent bien les résultats attendus.
- [ ] Chaque discipline sur la fiche club/coach affiche correctement ses badges **Indoor/Outdoor**, ses **métriques** (`Puissance`, `Allure /500m`, `Répétitions`…), ses **niveaux** et **coachs**.
- [ ] Un compte dont l'e-mail n'est pas vérifié est invité à vérifier son e-mail lorsqu'il tente de soumettre une demande (`403`).
- [ ] Après soumission (`201`), la carte passe immédiatement à **« En attente »** et apparaît dans *Mes demandes*.
- [ ] L'annulation (`DELETE /v1/me/join-requests/{id}`) réactive le bouton **« Demander l'adhésion »**.
- [ ] Après approbation dans le back-office Web par le club/coach, `GET /v1/me` retourne bien la nouvelle organisation dans `memberships` (avec les `group_ids` assignés) et la notification `join_request_approved` apparaît dans `GET /v1/me/notifications`.
