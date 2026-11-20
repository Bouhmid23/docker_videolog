"""Tests P1-P5 : KPIs équipe, session live liée au calendrier, partage multi-clubs, notifications et bilan."""
from datetime import UTC, datetime

import pytest

import db as db_mod


@pytest.mark.asyncio
async def test_p1_athletes_kpis_and_p5_report(client, mock_claims):
    await db_mod.users_col.insert_many(
        [
            {"_id": "coach-p", "email": "coach@p.fr", "first_name": "Coach", "last_name": "P"},
            {"_id": "ath-p1", "email": "ath1@p.fr", "first_name": "Alice", "last_name": "One"},
            {"_id": "ath-p2", "email": "ath2@p.fr", "first_name": "Bob", "last_name": "Two"},
        ]
    )
    await db_mod.orgs_col.insert_one(
        {"_id": "org-p", "type": "club", "name": "Club P", "owner_uid": "coach-p", "status": "active"}
    )
    await db_mod.memberships_col.insert_many(
        [
            {"org_id": "org-p", "uid": "coach-p", "role": "coach", "status": "active", "group_ids": []},
            {"org_id": "org-p", "uid": "ath-p1", "role": "athlete", "status": "active", "group_ids": []},
            {"org_id": "org-p", "uid": "ath-p2", "role": "athlete", "status": "active", "group_ids": []},
        ]
    )
    now = datetime.now(UTC)
    await db_mod.activities_col.insert_one(
        {
            "_id": "act-p1",
            "org_id": "org-p",
            "uid": "ath-p1",
            "sport": "cyclisme",
            "title": "Sortie P1",
            "start_time": now,
            "duration_s": 3600,
            "distance_m": 30000,
            "source": "fit_upload",
            "metrics": {"tss": 75.0, "mean_max_power": {"5": 800, "60": 420}},
            "created_at": now,
        }
    )

    mock_claims.update({"uid": "coach-p", "email": "coach@p.fr"})
    r_kpis = await client.get("/v1/orgs/org-p/analytics/athletes-kpis")
    assert r_kpis.status_code == 200
    body = r_kpis.json()
    assert body["team"]["athletes_count"] == 2
    assert body["team"]["total_tss_7d"] == 75.0

    r_rep = await client.get("/v1/orgs/org-p/analytics/report?uid=ath-p1&days=30")
    assert r_rep.status_code == 200
    rep = r_rep.json()
    assert rep["activities"]["count"] == 1
    assert rep["activities"]["total_tss"] == 75.0
    assert rep["records_power_w"]["5s"] == 800


@pytest.mark.asyncio
async def test_p2_live_from_planned_p3_sharing_and_p4_notifications(client, mock_claims):
    await db_mod.users_col.insert_many(
        [
            {"_id": "coach-1", "email": "c1@test.fr", "first_name": "Coach", "last_name": "1"},
            {"_id": "coach-2", "email": "c2@test.fr", "first_name": "Coach", "last_name": "2"},
            {"_id": "ath-s", "email": "aths@test.fr", "first_name": "Ath", "last_name": "Shared"},
        ]
    )
    await db_mod.orgs_col.insert_many(
        [
            {"_id": "org-1", "type": "club", "name": "Club 1", "owner_uid": "coach-1", "status": "active"},
            {"_id": "org-2", "type": "club", "name": "Club 2", "owner_uid": "coach-2", "status": "active"},
        ]
    )
    await db_mod.memberships_col.insert_many(
        [
            {"org_id": "org-1", "uid": "coach-1", "role": "owner", "status": "active", "group_ids": []},
            {"org_id": "org-2", "uid": "coach-2", "role": "owner", "status": "active", "group_ids": []},
            {"org_id": "org-1", "uid": "ath-s", "role": "athlete", "status": "active", "group_ids": []},
            {"org_id": "org-2", "uid": "ath-s", "role": "athlete", "status": "active", "group_ids": []},
        ]
    )

    # P2: planned workout -> live session
    await db_mod.planned_workouts_col.insert_one(
        {
            "_id": "pw-1",
            "org_id": "org-1",
            "uid": "ath-s",
            "title": "Seuil 3x10",
            "sport": "cyclisme",
            "date": "2026-11-16",
            "estimated_duration_s": 3600,
            "estimated_tss": 80,
            "steps": [],
        }
    )
    mock_claims.update({"uid": "coach-1", "email": "c1@test.fr"})
    r_sess = await client.post(
        "/v1/orgs/org-1/sessions",
        json={"title": "Seuil 3x10", "planned_workout_id": "pw-1"},
    )
    assert r_sess.status_code == 201
    assert r_sess.json()["session"]["planned_workout_id"] == "pw-1"
    assert "ath-s" in r_sess.json()["session"]["participant_uids"]

    # P3: athlete shares an activity from org-1 to org-2
    now = datetime.now(UTC)
    await db_mod.activities_col.insert_one(
        {
            "_id": "act-shared",
            "org_id": "org-1",
            "uid": "ath-s",
            "sport": "cyclisme",
            "title": "Sortie Partagee",
            "start_time": now,
            "duration_s": 1800,
            "distance_m": 15000,
            "source": "fit_upload",
            "metrics": {"tss": 40.0},
            "created_at": now,
        }
    )
    mock_claims.update({"uid": "ath-s", "email": "aths@test.fr"})
    r_share = await client.patch(
        "/v1/orgs/org-1/activities/act-shared/sharing",
        json={"shared_org_ids": ["org-2"]},
    )
    assert r_share.status_code == 200
    assert r_share.json()["activity"]["shared_org_ids"] == ["org-2"]

    mock_claims.update({"uid": "coach-2", "email": "c2@test.fr"})
    r_list2 = await client.get("/v1/orgs/org-2/activities")
    assert r_list2.status_code == 200
    assert [a["id"] for a in r_list2.json()["items"]] == ["act-shared"]

    # P4: coach-1 comments on act-shared -> ath-s gets notification
    mock_claims.update({"uid": "coach-1", "email": "c1@test.fr"})
    r_comm = await client.post(
        "/v1/orgs/org-1/activities/act-shared/comments",
        json={"body": "Bravo belle séance !"},
    )
    assert r_comm.status_code == 201

    mock_claims.update({"uid": "ath-s", "email": "aths@test.fr"})
    r_notif = await client.get("/v1/me/notifications")
    assert r_notif.status_code == 200
    assert r_notif.json()["unread_count"] == 1
    assert r_notif.json()["items"][0]["type"] == "activity_comment"

    r_read_all = await client.post("/v1/me/notifications/read-all")
    assert r_read_all.status_code == 200
    r_notif_after = await client.get("/v1/me/notifications")
    assert r_notif_after.json()["unread_count"] == 0


@pytest.mark.asyncio
async def test_calendar_hour_live_room_window_and_lobby(client, mock_claims):
    """L'horaire du calendrier se répercute sur la room LiveKit et son lobby, tout en restant ouvert si des participants sont connectés."""
    from datetime import timedelta

    await db_mod.users_col.insert_many(
        [
            {"_id": "coach-h", "email": "coach@h.fr", "first_name": "Coach", "last_name": "H"},
            {"_id": "ath-h", "email": "ath@h.fr", "first_name": "Ath", "last_name": "H"},
        ]
    )
    await db_mod.orgs_col.insert_one(
        {"_id": "org-h", "type": "club", "name": "Club Horaire", "owner_uid": "coach-h", "status": "active"}
    )
    await db_mod.memberships_col.insert_many(
        [
            {"org_id": "org-h", "uid": "coach-h", "role": "owner", "status": "active", "group_ids": []},
            {"org_id": "org-h", "uid": "ath-h", "role": "athlete", "status": "active", "group_ids": []},
        ]
    )

    mock_claims.update({"uid": "coach-h", "email": "coach@h.fr"})
    # 1. Planifier une séance à 18:30
    r_pw = await client.post(
        "/v1/orgs/org-h/workouts/planned",
        json={"uid": "ath-h", "date": "2026-11-16", "start_time": "18:30", "title": "VMA 18h30"},
    )
    assert r_pw.status_code == 201
    pw_id = r_pw.json()["planned_workout"]["id"]
    assert r_pw.json()["planned_workout"]["start_time"] == "18:30"

    # 2. Créer une session live liée avec horaire futur -> lobby & token OK
    future_start = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
    r_sess = await client.post(
        "/v1/orgs/org-h/sessions",
        json={
            "title": "VMA 18h30",
            "planned_workout_id": pw_id,
            "scheduled_at": future_start,
            "duration_s": 3600,
        },
    )
    assert r_sess.status_code == 201
    room_name = r_sess.json()["session"]["room_name"]
    assert r_sess.json()["session"]["duration_s"] == 3600

    r_lobby = await client.get(f"/rooms/{room_name}/lobby")
    assert r_lobby.status_code == 200
    lobby = r_lobby.json()
    assert lobby["accessible"] is True
    assert lobby["org_name"] == "Club Horaire"
    assert len(lobby["invited_participants"]) == 2

    # 3. Si l'horaire est dépassé et count == 0 -> lobby accessible=False, /token -> 410
    past_end = datetime.now(UTC) - timedelta(minutes=10)
    await db_mod.rooms.update_one(
        {"room_name": room_name},
        {"$set": {"scheduled_end_at": past_end, "count": 0}},
    )
    r_lobby_exp = await client.get(f"/rooms/{room_name}/lobby")
    assert r_lobby_exp.status_code == 200
    assert r_lobby_exp.json()["accessible"] is False
    r_tok_exp = await client.post("/token", json={"room": room_name, "client": "web"})
    assert r_tok_exp.status_code == 410

    # 4. Si au moins 1 participant est connecté (count > 0), la room reste accessible même après la fin !
    await db_mod.rooms.update_one({"room_name": room_name}, {"$set": {"count": 1}})
    r_lobby_live = await client.get(f"/rooms/{room_name}/lobby")
    assert r_lobby_live.status_code == 200
    assert r_lobby_live.json()["accessible"] is True
    r_tok_live = await client.post("/token", json={"room": room_name, "client": "web"})
    assert r_tok_live.status_code == 200


@pytest.mark.asyncio
async def test_finish_room_strict_rules_and_workout_comments(client, mock_claims):
    """Fin explicite de room, blocage accès, validation horaire, anti-doublon et commentaires séance."""
    from datetime import timedelta

    await db_mod.users_col.insert_many(
        [
            {"_id": "coach-r", "email": "coach@r.fr", "first_name": "Coach", "last_name": "R"},
            {"_id": "ath-r", "email": "ath@r.fr", "first_name": "Ath", "last_name": "R"},
        ]
    )
    await db_mod.orgs_col.insert_one(
        {"_id": "org-r", "type": "club", "name": "Club Regles", "owner_uid": "coach-r", "status": "active"}
    )
    await db_mod.memberships_col.insert_many(
        [
            {"org_id": "org-r", "uid": "coach-r", "role": "owner", "status": "active", "group_ids": []},
            {"org_id": "org-r", "uid": "ath-r", "role": "athlete", "status": "active", "group_ids": []},
        ]
    )

    mock_claims.update({"uid": "coach-r", "email": "coach@r.fr"})

    # 1. Validation stricte start_time (25:00 -> 422)
    tomorrow = (datetime.now(UTC).date() + timedelta(days=1)).isoformat()
    r_bad_time = await client.post(
        "/v1/orgs/org-r/workouts/planned",
        json={"uid": "ath-r", "date": tomorrow, "start_time": "25:00", "title": "Bad"},
    )
    assert r_bad_time.status_code == 422

    r_pw = await client.post(
        "/v1/orgs/org-r/workouts/planned",
        json={"uid": "ath-r", "date": tomorrow, "start_time": "17:30", "title": "Seance Test"},
    )
    assert r_pw.status_code == 201
    pw_id = r_pw.json()["planned_workout"]["id"]

    # 2. Commentaire coach sur la séance planifiée -> notification pour l'athlète
    r_c = await client.post(
        f"/v1/orgs/org-r/workouts/planned/{pw_id}/comments",
        json={"body": "Pense à bien t'échauffer 15 min."},
    )
    assert r_c.status_code == 201
    r_c_list = await client.get(f"/v1/orgs/org-r/workouts/planned/{pw_id}/comments")
    assert len(r_c_list.json()["items"]) == 1

    mock_claims.update({"uid": "ath-r", "email": "ath@r.fr"})
    r_notif = await client.get("/v1/me/notifications")
    assert r_notif.json()["unread_count"] == 1
    assert r_notif.json()["items"][0]["type"] == "workout_comment"

    # 3. Anti-doublon room sur un même planned_workout_id
    mock_claims.update({"uid": "coach-r", "email": "coach@r.fr"})
    future_start = (datetime.now(UTC) + timedelta(minutes=10)).isoformat()
    s1 = await client.post(
        "/v1/orgs/org-r/sessions",
        json={"title": "Seance Test", "planned_workout_id": pw_id, "scheduled_at": future_start},
    )
    s2 = await client.post(
        "/v1/orgs/org-r/sessions",
        json={"title": "Seance Test", "planned_workout_id": pw_id, "scheduled_at": future_start},
    )
    assert s1.status_code == 201
    assert s2.status_code == 201
    room_name = s1.json()["session"]["room_name"]
    assert s2.json()["session"]["room_name"] == room_name

    # 4. Terminer la session -> bloquée partout (token, lobby, mobile/rooms, session detail)
    r_fin = await client.post(f"/v1/orgs/org-r/sessions/{room_name}/finish")
    assert r_fin.status_code == 200
    assert r_fin.json()["status"] == "finished"

    assert (await client.post("/token", json={"room": room_name, "client": "web"})).status_code == 404
    lobby_after = await client.get(f"/rooms/{room_name}/lobby")
    assert lobby_after.json()["accessible"] is False
    assert lobby_after.json()["finished"] is True
    mob_rooms = await client.get("/mobile/rooms")
    assert room_name not in [r["room_name"] for r in mob_rooms.json()["rooms"]]
    det = await client.get(f"/v1/orgs/org-r/sessions/{room_name}")
    assert det.json()["joinable"] is False


@pytest.mark.asyncio
async def test_past_workout_comment_blocked_and_coach_profile(client, mock_claims):
    """Commentaire refusé sur une séance passée et mise à jour du profil entraîneur."""
    from datetime import timedelta

    await db_mod.users_col.insert_many(
        [
            {"_id": "coach-cp", "email": "coach-cp@r.fr", "first_name": "Coach", "last_name": "CP"},
            {"_id": "ath-cp", "email": "ath-cp@r.fr", "first_name": "Ath", "last_name": "CP"},
        ]
    )
    await db_mod.orgs_col.insert_one(
        {"_id": "org-cp", "type": "club", "name": "Club CP", "owner_uid": "coach-cp", "status": "active"}
    )
    await db_mod.memberships_col.insert_many(
        [
            {"org_id": "org-cp", "uid": "coach-cp", "role": "coach", "status": "active", "group_ids": []},
            {"org_id": "org-cp", "uid": "ath-cp", "role": "athlete", "status": "active", "group_ids": []},
        ]
    )
    mock_claims.update({"uid": "coach-cp", "email": "coach-cp@r.fr", "email_verified": True})

    yesterday = (datetime.now(UTC).date() - timedelta(days=1)).isoformat()
    r_pw = await client.post(
        "/v1/orgs/org-cp/workouts/planned",
        json={"uid": "ath-cp", "date": yesterday, "title": "Séance d'hier"},
    )
    assert r_pw.status_code == 201
    pw_id = r_pw.json()["planned_workout"]["id"]

    r_comment = await client.post(
        f"/v1/orgs/org-cp/workouts/planned/{pw_id}/comments",
        json={"body": "Message tardif"},
    )
    assert r_comment.status_code == 400

    r_cp = await client.put(
        "/v1/me/coach-profile",
        json={
            "sports": [
                {"sport": "cyclisme", "level": "nationale", "years_experience": 8},
                {"sport": "triathlon", "level": "compétition régionale", "years_experience": 4},
            ],
            "certifications": "DEJEPS Cyclisme",
            "bio": "Spécialiste contre-la-montre et gestion de charge.",
        },
    )
    assert r_cp.status_code == 200
    cp = r_cp.json()["user"]["coach_profile"]
    assert len(cp["sports"]) == 2
    assert cp["sports"][0]["sport"] == "cyclisme"
    assert cp["sports"][0]["level"] == "nationale"
    assert cp["certifications"] == "DEJEPS Cyclisme"

    mock_claims.update({"uid": "ath-cp", "email": "ath-cp@r.fr", "email_verified": True})
    r_me_ath = await client.get("/v1/me")
    assert r_me_ath.status_code == 200
    coaches = r_me_ath.json()["memberships"][0]["coaches"]
    assert len(coaches) == 1
    assert coaches[0]["uid"] == "coach-cp"
    assert coaches[0]["coach_profile"]["certifications"] == "DEJEPS Cyclisme"



@pytest.mark.asyncio
async def test_gdpr_export_plan_patch_and_health_consent_wellness_purge(client, mock_claims):
    """Vérifie l'export RGPD /v1/me/export, la purge wellness au retrait du consentement et PATCH /plans/{id}."""
    await db_mod.users_col.insert_one(
        {
            "_id": "coach-plan",
            "email": "coach-plan@r.fr",
            "first_name": "Coach",
            "last_name": "Plan",
            "health_consent": {"granted": True, "version": "2026-11-v1"},
            "athlete_profile": {"sport": "cyclisme", "hr_max": 190, "hr_rest": 50, "weight_kg": 72.0},
        }
    )
    await db_mod.orgs_col.insert_one(
        {"_id": "org-plan", "type": "club", "name": "Club Plan", "owner_uid": "coach-plan", "status": "active"}
    )
    await db_mod.memberships_col.insert_one(
        {"org_id": "org-plan", "uid": "coach-plan", "role": "owner", "status": "active", "group_ids": []}
    )
    await db_mod.wellness_entries_col.insert_one(
        {
            "_id": "w-1",
            "org_id": "org-plan",
            "uid": "coach-plan",
            "date": "2026-11-20",
            "sleep_h": 7.5,
            "hrv_ms": 68,
            "resting_hr": 49,
            "weight_kg": 72.0,
        }
    )
    mock_claims.update({"uid": "coach-plan", "email": "coach-plan@r.fr", "email_verified": True})

    # 1. Créer puis modifier un plan d'entraînement (PATCH /plans/{id})
    r_create = await client.post(
        "/v1/orgs/org-plan/plans",
        json={
            "name": "Plan Initial",
            "sport": "cyclisme",
            "weeks": 2,
            "items": [
                {
                    "day_offset": 0,
                    "title": "Séance J0",
                    "sport": "cyclisme",
                    "steps": [{"kind": "active", "duration_s": 1800, "target_type": "ftp_pct", "target_min": 80, "target_max": 90}],
                }
            ],
        },
    )
    assert r_create.status_code == 201
    plan_id = r_create.json()["plan"]["id"]

    r_patch = await client.patch(
        f"/v1/orgs/org-plan/plans/{plan_id}",
        json={
            "name": "Plan Modifié 4 semaines",
            "sport": "cyclisme",
            "weeks": 4,
            "items": [],
        },
    )
    assert r_patch.status_code == 200
    assert r_patch.json()["plan"]["name"] == "Plan Modifié 4 semaines"
    assert r_patch.json()["plan"]["weeks"] == 4

    # 2. Export RGPD (/v1/me/export)
    r_exp = await client.get("/v1/me/export")
    assert r_exp.status_code == 200
    exp_data = r_exp.json()
    assert exp_data["user"]["uid"] == "coach-plan"
    assert len(exp_data["wellness_entries"]) == 1
    assert exp_data["wellness_entries"][0]["hrv_ms"] == 68

    # 3. Révocation du consentement santé -> purge hrv_ms, resting_hr, weight_kg dans wellness_entries
    r_rev = await client.delete("/v1/me/health-consent")
    assert r_rev.status_code == 200
    w_after = await db_mod.wellness_entries_col.find_one({"_id": "w-1"})
    assert w_after["hrv_ms"] is None
    assert w_after["resting_hr"] is None
    assert w_after["weight_kg"] is None
    assert w_after["sleep_h"] == 7.5
