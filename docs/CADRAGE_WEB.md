# Cadrage fonctionnel & technique — Composante Web (Back-office & Salon Live)

## 1. Positionnement de la composante Web

La composante Web (`backoffice/` + `/live/`) est l'**espace principal de conception, d'analyse, d'administration et de supervision en direct** sur grand écran.

- **Public principal** : Coachs, Administrateurs / Propriétaires de club.
- **Public secondaire** : Athlètes en consultation bureau (bilan, calendrier, relecture vidéo détaillée, acceptation d'invitations).

> **Principe** : Le Web ne cherche pas à remplacer la capture terrain du smartphone (capteurs BLE, GPS, caméra embarquée), mais offre toute la puissance d'un poste de pilotage et d'analyse.

---

## 2. Périmètre fonctionnel Web

### 2.1 Espace Back-office (`/app/*`)

| Module | Page | Fonctionnalités incluses | Rôles |
|---|---|---|---|
| **Tableau de bord** | `DashboardPage.tsx` | KPIs club/coach, suivi charge & forme des athlètes, drapeaux d'alerte (`overload`, `high_ramp`, `high_fatigue`, `missed_workouts`) | Staff / Athlète (vue synthétique) |
| **Calendrier** | `CalendarPage.tsx` | Vue hebdo, glisser-déposer, planification avec **heure de début** (`start_time`), tri chronologique, export `.fit`/`.zwo`, modale **Détails & discussion** coach ↔ athlète, lancement **Démarrer en live** | Staff (gestion) / Athlète (lecture & discussion) |
| **Bibliothèque & Plans** | `WorkoutsLibraryPage.tsx`, `TrainingPlansPage.tsx` | Éditeur de séances structurées (blocs, répétitions, zones % FTP / % FC), plans multi-semaines appliqués à un athlète ou groupe | Staff |
| **Forme & Charge** | `PerformancePage.tsx` | Courbes `uPlot` (**Forme de fond 42j**, **Fatigue récente 7j**, **Fraîcheur**, **Progression hebdo**), Records puissance/cardio, journal de forme, **Comparateur & KPIs d'équipe** (filtré `athlete`), **Bilan exportable / imprimable PDF** | Staff (multi-athlètes) / Athlète (ses données) |
| **Activités** | `ActivitiesPage.tsx`, `ActivityDetailPage.tsx` | Upload `.fit`/`.gpx`/`.tcx`, liste filtrée par sport/athlète, métriques détaillées, zones, tours, commentaires, **partage multi-clubs/coachs** | Staff / Athlète |
| **Sessions Live & Replay** | `SessionsPage.tsx`, `SessionDetailPage.tsx` | Création de session, statut (`Active` / `Expirée` / `Terminée`), bouton **Rejoindre en direct** (si `joinable`), bouton **Terminer la session**, lecteur vidéo synchronisé avec télémétrie `uPlot`, marqueurs coach, alertes | Staff / Athlète |
| **Club & Équipe** | `MembersPage.tsx`, `GroupsPage.tsx`, `InvitationsPage.tsx`, `OrgSettingsPage.tsx` | Gestion membres, groupes, invitations par e-mail (avec ou sans groupe pour un coach), paramètres d'organisation | Admin / Owner / Coach (scope) |
| **Profil & Multi-clubs** | `MyProfilePage.tsx`, `AppLayout.tsx` | Profil sportif, consentement santé RGPD, seuils datés, bannière **invitations en attente** in-app, section **Mes clubs & coachs** (basculer / quitter), **cloche de notifications** | Tous |

### 2.2 Salon Live Web (`/live/`)

| Fonctionnalité | Détail | Fichiers |
|---|---|---|
| **Lobby enrichi (sans champ brut)** | Lecture via `?room=` (ou sélecteur de séances joignables via `/api/mobile/rooms`), affichage titre, club/coach, horaire & durée, liste des participants invités (statut en ligne), **aperçu vidéo local** avant d'entrer, blocage + lien replay si session terminée/expirée | `backoffice/public/live/index.html`, `js/salon/room.js` |
| **Supervision en direct** | Grille vidéo des participants, cartes télémétrie temps réel (vitesse, FC, cadence, GPS), posture/avatar, contrôle d'enregistrement Egress | `js/salon/*.js`, `js/telemetry-ui.js` |
| **Séance structurée en direct** | Bandeau `#workoutBanner` affichant l'étape active, la cible (`W` / `bpm` / `% FTP`), le minuteur, l'étape suivante, et contrôles coach **Préc. / Pause / Suiv.** synchronisés via DataTrack (`topic: "workout_step"`) | `js/salon/room.js`, `css/salon.css` |

---

## 3. Hors périmètre Web (réservé au Mobile)

- Connexion directe à des capteurs **BLE** (cardio, puissance, home trainer FTMS/ERG).
- Collecte **GPS** et télémétrie terrain en mobilité.
- Synchronisation **Apple HealthKit** / **Android Health Connect**.
- Notifications push natives OS (FCM / APNs).

---

## 4. Règles métier & sécurité Web

1. **Isolation des rôles** : les sélecteurs de KPIs, courbes, activités et calendrier côté coach ne proposent que les membres `role === "athlete"` (`?role=athlete`, `athletes_only=true`).
2. **Accès aux rooms** :
   - Une room terminée (`finished: true`) ou expirée (`scheduled_end_at` dépassé avec `count == 0`) est **strictement inaccessible** (`404`/`410`, bouton Rejoindre masqué).
   - Une room avec au moins 1 participant connecté (`count > 0`) reste accessible même après l'horaire prévu, jusqu'à la déconnexion ou l'action **Terminer la session**.
   - Pas de doublon de room active pour un même `planned_workout_id`.
3. **Cache navigateur** : `nginx.conf` applique un cache long sur les assets statiques ; toute mise à jour Web nécessite un rechargement fort (`Ctrl+Shift+R`) ou un hash de build Vite.

---

## 5. Critères d'acceptation Web

- `npm run typecheck`, `npm run lint`, `npm run build` passent sans erreur dans `backoffice/`.
- Tous les parcours coach (planifier avec heure → discuter → démarrer en live → superviser avec bandeau séance → terminer la session → analyser replay/bilan) fonctionnent sans erreur console.
