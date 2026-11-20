# Cadrage fonctionnel & technique — Composante Mobile (Flutter)

## 1. Positionnement de la composante Mobile

L'application mobile Flutter est l'**outil terrain et quotidien de l'athlète** (et ponctuellement du coach en mobilité) :

- **Capture en direct** : caméra, micro, GPS et capteurs physiologiques/puissance (BLE) vers LiveKit.
- **Routine quotidienne rapide** : consulter la séance du jour, saisir sa forme en 10 secondes, noter son effort ressenti (RPE), lire les messages du coach.
- **Passerelle santé & montres** : synchronisation **Apple HealthKit** / **Android Health Connect** et upload rapide de fichiers `.fit`.

> **Principe** : Ne pas porter sur mobile les écrans complexes d'édition de plans multi-semaines, de calendrier glisser-déposer ou de graphiques multi-séries lourds (réservés au Web).

---

## 2. Périmètre fonctionnel Mobile (par priorité)

### Lot 1 — Fiabilité Live, Contrôles & Multi-clubs (Prioritaire)

| Fonctionnalité | Comportement attendu côté Mobile | Contrat API |
|---|---|---|
| **Liste des rooms joignables** | Afficher uniquement les sessions actives/joignables (le backend filtre déjà `finished` et `scheduled_end_at` dépassé sans participants) | `GET /mobile/rooms` |
| **Gestion `410 Gone` & `404`** | Si `POST /token` renvoie `410` (horaire dépassé, room vide) ou `404` (terminée), afficher le `detail` et rafraîchir `/mobile/rooms` | `POST /token` |
| **Fermeture en direct par le coach** | Sur événement LiveKit `RoomDisconnectedEvent`, quitter la room proprement et rafraîchir la liste | LiveKit SDK |
| **Invitations in-app (N-N)** | Afficher les invitations reçues, permettre **Accepter** / **Refuser** en 1 clic, puis rafraîchir `GET /v1/me` | `GET /v1/me/invitations`, `POST /v1/me/invitations/{id}/accept`, `.../decline` |
| **Switch, départ & fiche coach** | Sélecteur d'organisation active (`memberships`), option « Quitter ce club/coach » et affichage de la fiche coach (`coaches[].coach_profile` : sports, niveaux, diplômes, bio) | `GET /v1/me`, `DELETE /v1/orgs/{org_id}/members/me` |

### Lot 2 — Quotidien Athlète & Messagerie

| Fonctionnalité | Comportement attendu côté Mobile | Contrat API |
|---|---|---|
| **Séance du jour** | Écran d'accueil affichant la séance planifiée du jour (`start_time`, étapes par type/durée/intensité simple + `note`, `pts d'effort` au lieu de `TSS`) + export `.fit` | `GET /v1/orgs/{org_id}/calendar?from=...&to=...` |
| **Forme du jour (10s)** | Formulaire rapide (sommeil, qualité, fatigue, courbatures, stress) — saisi exclusivement sur Mobile | `POST /v1/orgs/{org_id}/wellness` |
| **RPE & ressenti post-séance** | Saisie rapide de l'effort ressenti (`rpe` 1–10) et commentaire sur l'activité | `PATCH /v1/orgs/{org_id}/activities/{id}` |
| **Notifications & discussions** | Badge cloche + consultation/réponse aux messages sur séances planifiées et activités (lecture seule sur séance planifiée passée : l'API renvoie `400` si expirée) | `GET /v1/me/notifications`, `POST /v1/me/notifications/read-all`, `GET/POST .../workouts/planned/{id}/comments`, `GET/POST .../activities/{id}/comments` |
| **Partage multi-clubs** | Choisir avec quels autres clubs/coachs partager une activité | `PATCH /v1/orgs/{org_id}/activities/{id}/sharing` |

### Lot 3 — Terrain Avancé & Capteurs (Phase 4–5)

| Fonctionnalité | Comportement attendu côté Mobile | Contrat API / Protocole |
|---|---|---|
| **Bandeau séance en direct** | Afficher l'étape active et le minuteur synchronisé avec le coach web | `GET /rooms/{room_name}/lobby` + DataTrack LiveKit topic `"workout_step"` (`{ idx, remaining_s, paused }`) |
| **HealthKit / Health Connect** | Import automatique des activités, sommeil, VFC, FC repos, poids (après consentement RGPD) | `POST /v1/orgs/{org_id}/activities/upload`, `POST /v1/orgs/{org_id}/wellness` |
| **Capteurs BLE & Télémétrie v3** | Collecte ceinture cardio (`0x180D`), capteur puissance (`0x1818`), home trainer FTMS (`0x1826`) | DataTrack LiveKit `telemetry` (évolution paquet v3 avec puissance) |
| **Push notifications** | Réception de notifications FCM/APNs (séance du jour, nouveau message coach) | Firebase Cloud Messaging |

---

## 3. Hors périmètre Mobile (réservé au Web)

- Éditeur visuel complet de séances structurées et plans multi-semaines.
- Calendrier hebdomadaire glisser-déposer multi-athlètes.
- Comparateur multi-athlètes, courbes `uPlot` multi-séries et bilans PDF imprimables.
- Relecture vidéo avec annotation de marqueurs image par image.
- Administration complète du club (groupes, rôles, facturation).

---

## 4. Contraintes techniques & prérequis réseau

1. **Identité LiveKit** : le mobile ne passe pas `client: "web"` à `POST /token` (il conserve son `uid` brut ; le navigateur utilise `{uid}:web` pour éviter tout conflit `DUPLICATE_IDENTITY` si l'utilisateur est connecté sur les deux).
2. **Lecture vidéo MinIO (dev local)** :
   - En USB sur téléphone physique avec `MINIO_PUBLIC_URL=http://localhost:9000`, activer `adb reverse tcp:9000 tcp:9000` pour ExoPlayer.
   - En Wi-Fi / pré-prod, utiliser une URL accessible (`MINIO_PUBLIC_URL=http://<IP_LAN>:9000` ou S3 HTTPS managé).
3. **Consentement santé RGPD** : toute écriture de FC max/repos, VFC ou poids nécessite `health_consent.granted == true` (`PUT /v1/me/athlete-profile`).

---

## 5. Références existantes

- Guide d'intégration technique détaillé : [MOBILE_CLIENT_GUIDE.md](file:///d:/DOWNLOADS/videolog-poc-fixed/videolog-poc-fixed/docs/MOBILE_CLIENT_GUIDE.md)
- Guide MVP Flutter : [FLUTTER_MVP_INTEGRATION_GUIDE.md](file:///d:/DOWNLOADS/videolog-poc-fixed/videolog-poc-fixed/docs/FLUTTER_MVP_INTEGRATION_GUIDE.md)
