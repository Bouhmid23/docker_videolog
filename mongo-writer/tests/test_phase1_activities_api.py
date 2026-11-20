"""Tests d'intégration API Phase 1 : seuils datés, upload/liste/détail/streams/commentaires activités, conversion live."""
from datetime import UTC, datetime
import io

from garmin_fit_sdk import Encoder, Profile
import pytest

import db as db_mod


def _make_sample_fit() -> bytes:
    t0 = datetime(2026, 11, 12, 9, 0, 0, tzinfo=UTC)
    enc = Encoder()
    enc.on_mesg(
        Profile["mesg_num"]["FILE_ID"],
        {"type": "activity", "manufacturer": "development", "product": 1, "time_created": t0},
    )
    from datetime import timedelta

    for i in range(40):
        enc.on_mesg(
            Profile["mesg_num"]["RECORD"],
            {
                "timestamp": t0 + timedelta(seconds=i),
                "heart_rate": 152,
                "power": 260,
                "cadence": 92,
                "enhanced_speed": 10.0,
            },
        )
    enc.on_mesg(
        Profile["mesg_num"]["SESSION"],
        {
            "timestamp": t0 + timedelta(seconds=39),
            "start_time": t0,
            "sport": "cycling",
            "total_elapsed_time": 40.0,
            "total_timer_time": 40.0,
            "total_distance": 400.0,
        },
    )
    return bytes(enc.close())


async def _seed_org_and_users():
    await db_mod.users_col.insert_many(
        [
            {"_id": "coach-1", "email": "coach@club.fr", "first_name": "Coach", "last_name": "Un"},
            {"_id": "ath-1", "email": "ath1@club.fr", "first_name": "Alice", "last_name": "Durand"},
            {"_id": "ath-2", "email": "ath2@club.fr", "first_name": "Bob", "last_name": "Martin"},
        ]
    )
    await db_mod.orgs_col.insert_one(
        {"_id": "org-1", "type": "club", "name": "Club Test", "owner_uid": "coach-1", "status": "active"}
    )
    await db_mod.memberships_col.insert_many(
        [
            {"org_id": "org-1", "uid": "coach-1", "role": "coach", "status": "active", "group_ids": []},
            {"org_id": "org-1", "uid": "ath-1", "role": "athlete", "status": "active", "group_ids": []},
            {"org_id": "org-1", "uid": "ath-2", "role": "athlete", "status": "active", "group_ids": []},
        ]
    )


@pytest.mark.asyncio
async def test_thresholds_and_activities_lifecycle(client, mock_claims):
    await _seed_org_and_users()
    mock_claims["uid"] = "coach-1"
    mock_claims["email"] = "coach@club.fr"

    # 1. Coach définit un FTP = 250 W pour ath-1
    r_th = await client.put(
        "/v1/orgs/org-1/athletes/ath-1/thresholds",
        json={
            "sport": "cyclisme",
            "effective_from": "2026-11-01",
            "ftp_w": 250,
            "lthr_bpm": 170,
            "hr_max": 192,
            "hr_rest": 48,
        },
    )
    assert r_th.status_code == 200
    assert len(r_th.json()["threshold"]["power_zones"]) == 7

    # 2. Coach importe un fichier FIT pour ath-1
    fit_data = _make_sample_fit()
    r_up = await client.post(
        "/v1/orgs/org-1/activities/upload",
        data={"athlete_uid": "ath-1", "title": "Bloc seuil"},
        files={"file": ("seance.fit", io.BytesIO(fit_data), "application/octet-stream")},
    )
    assert r_up.status_code == 201
    act = r_up.json()["activity"]
    act_id = act["id"]
    assert act["uid"] == "ath-1"
    assert act["metrics"]["avg_power"] == 260.0
    assert act["metrics"]["tss_method"] == "power"

    # Doublon rejeté en 409
    r_dup = await client.post(
        "/v1/orgs/org-1/activities/upload",
        data={"athlete_uid": "ath-1"},
        files={"file": ("seance.fit", io.BytesIO(fit_data), "application/octet-stream")},
    )
    assert r_dup.status_code == 409

    # 3. Flux 1 Hz avec downsample
    r_st = await client.get(f"/v1/orgs/org-1/activities/{act_id}/streams?downsample=2")
    assert r_st.status_code == 200
    assert len(r_st.json()["t"]) == 20

    # 4. PATCH RPE + commentaire coach
    r_patch = await client.patch(
        f"/v1/orgs/org-1/activities/{act_id}", json={"rpe": 8, "feeling": 4}
    )
    assert r_patch.status_code == 200
    assert r_patch.json()["activity"]["rpe"] == 8

    r_com = await client.post(
        f"/v1/orgs/org-1/activities/{act_id}/comments",
        json={"body": "Très bonne régularité sur les watts !"},
    )
    assert r_com.status_code == 201

    # 5. Isolation : ath-2 ne peut pas voir l'activité d'ath-1
    mock_claims["uid"] = "ath-2"
    r_forbid = await client.get(f"/v1/orgs/org-1/activities/{act_id}")
    assert r_forbid.status_code == 403

    r_list_ath2 = await client.get("/v1/orgs/org-1/activities")
    assert r_list_ath2.status_code == 200
    assert r_list_ath2.json()["total"] == 0

    # 6. ath-1 voit son activité et ses commentaires
    mock_claims["uid"] = "ath-1"
    r_get = await client.get(f"/v1/orgs/org-1/activities/{act_id}")
    assert r_get.status_code == 200
    assert len(r_get.json()["zones"]["power_zones"]) == 7

    r_coms = await client.get(f"/v1/orgs/org-1/activities/{act_id}/comments")
    assert len(r_coms.json()["items"]) == 1

    # 7. Suppression par coach
    mock_claims["uid"] = "coach-1"
    r_del = await client.delete(f"/v1/orgs/org-1/activities/{act_id}")
    assert r_del.status_code == 200
    assert await db_mod.activity_streams_col.count_documents({"activity_id": act_id}) == 0


@pytest.mark.asyncio
async def test_convert_live_session_to_activity(client, mock_claims):
    await _seed_org_and_users()
    mock_claims["uid"] = "coach-1"

    await db_mod.rooms.insert_one(
        {"room_name": "room-live-1", "org_id": "org-1", "title": "Home Trainer Live"}
    )
    await db_mod.col.insert_many(
        [
            {
                "room_name": "room-live-1",
                "participant": "ath-1",
                "server_ts": 1762940000000,
                "pts_ms": 0,
                "telemetry": {"role": "trainee", "speed": 30.0, "heart_rate": 150, "cadence": 90, "power_w": 240},
            },
            {
                "room_name": "room-live-1",
                "participant": "ath-1",
                "server_ts": 1762940030000,
                "pts_ms": 30000,
                "telemetry": {"role": "trainee", "speed": 32.0, "heart_rate": 158, "cadence": 92, "power_w": 260},
            },
        ]
    )
    await db_mod.recs.insert_one(
        {
            "egress_id": "eg-live-1",
            "room_name": "room-live-1",
            "status": "completed",
            "started_at": "2025-11-12T09:33:20+00:00",
            "files": [{"filename": "room-live-1/rec.mp4", "duration": 30.0}],
        }
    )

    resp = await client.post("/v1/orgs/org-1/activities/convert-live/room-live-1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["converted_count"] == 1
    act = data["activities"][0]
    assert act["source"] == "live"
    assert act["uid"] == "ath-1"
    assert act["distance_m"] > 200.0  # Intégration de la vitesse sans GPS en home-trainer

    detail = await client.get(f"/v1/orgs/org-1/activities/{act['id']}")
    assert detail.status_code == 200
    assert len(detail.json()["video_assets"]) == 1
    assert "minio.videolog.test" in detail.json()["video_assets"][0]["mp4_url"]


@pytest.mark.asyncio
async def test_indoor_sports_mapping_workout_reps_and_zwo_run(client, mock_claims):
    from services.ingestion import map_sport

    assert map_sport("cycling", "indoor_cycling") == "home_trainer"
    assert map_sport("cycling", "virtual_activity") == "home_trainer"
    assert map_sport("running", "treadmill") == "tapis_course"
    assert map_sport("rowing", "indoor_rowing") == "aviron_indoor"
    assert map_sport("training", "strength_training") == "musculation"
    assert map_sport("fitness_equipment", "hiit") == "cross_training"
    assert map_sport("training", "yoga") == "yoga_pilates"
    assert map_sport("boxing", "boxing") == "boxe_combat"
    assert map_sport("rock_climbing", "bouldering") == "escalade"

    await _seed_org_and_users()
    mock_claims["uid"] = "coach-1"

    # 1. Créer un modèle de séance Cross-Training avec durée en secondes et répétitions
    r_tpl = await client.post(
        "/v1/orgs/org-1/workouts/templates",
        json={
            "name": "WOD Tabata & Force",
            "sport": "cross_training",
            "steps": [
                {
                    "kind": "active",
                    "duration_s": 45,
                    "reps": 15,
                    "target_type": "open",
                    "target_min": 85,
                    "target_max": 95,
                    "note": "Thrusters",
                },
                {"kind": "rest", "duration_s": 15, "target_type": "open"},
            ],
        },
    )
    assert r_tpl.status_code == 201
    tpl = r_tpl.json()["template"]
    assert tpl["sport"] == "cross_training"
    assert tpl["estimated_duration_s"] == 60
    assert tpl["blocks"][0]["reps"] == 15

    # 2. Planifier une séance Tapis de course et exporter en .zwo (<sportType>run</sportType>)
    r_pw = await client.post(
        "/v1/orgs/org-1/workouts/planned",
        json={
            "uid": "ath-1",
            "date": "2026-11-20",
            "sport": "tapis_course",
            "title": "Seuil Tapis",
            "steps": [{"kind": "active", "duration_s": 600, "target_type": "pace_pct", "target_min": 95, "target_max": 100}],
        },
    )
    assert r_pw.status_code == 201
    pw_id = r_pw.json()["planned_workout"]["id"]

    r_zwo = await client.get(f"/v1/orgs/org-1/workouts/planned/{pw_id}/export?format=zwo")
    assert r_zwo.status_code == 200
    assert "<sportType>run</sportType>" in r_zwo.text

    # 3. Seuil /500m pour Rameur indoor
    r_th = await client.put(
        "/v1/orgs/org-1/athletes/ath-1/thresholds",
        json={
            "sport": "aviron_indoor",
            "effective_from": "2026-11-01",
            "ftp_w": 280,
            "pace_500m_s": 105,
        },
    )
    assert r_th.status_code == 200
    assert r_th.json()["threshold"]["pace_500m_s"] == 105


@pytest.mark.asyncio
async def test_finish_session_auto_converts_activity_and_replay_power_w(
    client, mock_claims
):
    await _seed_org_and_users()
    mock_claims["uid"] = "coach-1"

    s = await client.post(
        "/v1/orgs/org-1/sessions",
        json={"title": "Home-Trainer Seuil", "participant_uids": ["ath-1"]},
    )
    assert s.status_code == 201
    room_name = s.json()["session"]["room_name"]

    base_ts = 1_760_000_000_000.0
    await db_mod.col.insert_many(
        [
            {
                "room_name": room_name,
                "participant": "ath-1:web",
                "pts_ms": 0.0,
                "server_ts": base_ts,
                "telemetry": {
                    "role": "trainee",
                    "speed": 36.0,
                    "heart_rate": 152,
                    "cadence": 92,
                    "power_w": 245,
                },
            },
            {
                "room_name": room_name,
                "participant": "ath-1:web",
                "pts_ms": 30_000.0,
                "server_ts": base_ts + 30_000.0,
                "telemetry": {
                    "role": "trainee",
                    "speed": 36.0,
                    "heart_rate": 158,
                    "cadence": 94,
                    "power_w": 265,
                },
            },
        ]
    )

    # Le replay retourne power_w et normalise :web même pour l'athlète filtré par son uid
    mock_claims["uid"] = "ath-1"
    r_tel = await client.get(f"/v1/orgs/org-1/sessions/{room_name}/telemetry")
    assert r_tel.status_code == 200
    samples = r_tel.json()
    assert len(samples) == 2
    assert samples[0]["participant"] == "ath-1"
    assert samples[0]["power_w"] == 245
    assert samples[1]["power_w"] == 265

    # La clôture par le coach déclenche automatiquement la conversion Live -> Activité
    mock_claims["uid"] = "coach-1"
    r_fin = await client.post(f"/v1/orgs/org-1/sessions/{room_name}/finish")
    assert r_fin.status_code == 200
    fin_data = r_fin.json()
    assert fin_data["status"] == "finished"
    assert fin_data["converted_count"] == 1
    assert len(fin_data["activity_ids"]) == 1


