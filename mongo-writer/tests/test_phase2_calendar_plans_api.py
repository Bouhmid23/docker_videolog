"""Tests d'intégration API Phase 2 : bibliothèque, calendrier, plans multi-semaines, objectifs et appariement auto."""
from datetime import UTC, datetime, timedelta
import io

from garmin_fit_sdk import Encoder, Profile
import pytest

import db as db_mod


def _make_fit_on_date(day: datetime, duration_s: int = 300) -> bytes:
    enc = Encoder()
    enc.on_mesg(
        Profile["mesg_num"]["FILE_ID"],
        {"type": "activity", "manufacturer": "development", "product": 1, "time_created": day},
    )
    for i in range(duration_s):
        enc.on_mesg(
            Profile["mesg_num"]["RECORD"],
            {"timestamp": day + timedelta(seconds=i), "heart_rate": 155, "power": 240},
        )
    enc.on_mesg(
        Profile["mesg_num"]["SESSION"],
        {
            "timestamp": day + timedelta(seconds=duration_s),
            "start_time": day,
            "sport": "cycling",
            "total_elapsed_time": float(duration_s),
            "total_timer_time": float(duration_s),
            "total_distance": 15000.0,
        },
    )
    return bytes(enc.close())


async def _seed_club():
    await db_mod.users_col.insert_many(
        [
            {"_id": "coach-p2", "email": "coach@p2.fr", "first_name": "Coach", "last_name": "P2"},
            {"_id": "ath-p2", "email": "ath@p2.fr", "first_name": "Claire", "last_name": "Velo"},
        ]
    )
    await db_mod.orgs_col.insert_one(
        {"_id": "org-p2", "type": "club", "name": "Club P2", "owner_uid": "coach-p2", "status": "active"}
    )
    await db_mod.groups_col.insert_one(
        {"_id": "grp-p2", "org_id": "org-p2", "name": "Route", "sport": "cyclisme", "coach_uids": ["coach-p2"]}
    )
    await db_mod.memberships_col.insert_many(
        [
            {"org_id": "org-p2", "uid": "coach-p2", "role": "coach", "status": "active", "group_ids": ["grp-p2"]},
            {"org_id": "org-p2", "uid": "ath-p2", "role": "athlete", "status": "active", "group_ids": ["grp-p2"]},
        ]
    )


@pytest.mark.asyncio
async def test_workouts_calendar_plans_and_auto_matching(client, mock_claims):
    await _seed_club()
    mock_claims["uid"] = "coach-p2"

    # 1. Définir un FTP = 340 W pour ath-p2 (ainsi 240 W ≈ 70 % FTP) et créer un modèle
    await client.put(
        "/v1/orgs/org-p2/athletes/ath-p2/thresholds",
        json={"sport": "cyclisme", "effective_from": "2026-11-01", "ftp_w": 340},
    )
    r_tpl = await client.post(
        "/v1/orgs/org-p2/workouts/templates",
        json={
            "name": "Endurance 30min",
            "sport": "cyclisme",
            "tags": ["endurance", "z2"],
            "steps": [{"kind": "active", "duration_s": 300, "target_type": "ftp_pct", "target_min": 65, "target_max": 75}],
        },
    )
    assert r_tpl.status_code == 201
    tpl_id = r_tpl.json()["template"]["id"]

    r_dup = await client.post(f"/v1/orgs/org-p2/workouts/templates/{tpl_id}/duplicate")
    assert r_dup.status_code == 201

    # 2. Planifier la séance le 2026-11-18 puis la déplacer au 2026-11-19
    r_pw = await client.post(
        "/v1/orgs/org-p2/workouts/planned",
        json={"uid": "ath-p2", "date": "2026-11-18", "template_id": tpl_id},
    )
    assert r_pw.status_code == 201
    pw_id = r_pw.json()["planned_workout"]["id"]

    r_move = await client.patch(
        f"/v1/orgs/org-p2/workouts/planned/{pw_id}",
        json={"date": "2026-11-19"},
    )
    assert r_move.status_code == 200
    assert r_move.json()["planned_workout"]["date"] == "2026-11-19"

    # Export .fit et .zwo
    r_fit = await client.get(f"/v1/orgs/org-p2/workouts/planned/{pw_id}/export?format=fit")
    assert r_fit.status_code == 200
    assert len(r_fit.content) > 50

    r_zwo = await client.get(f"/v1/orgs/org-p2/workouts/planned/{pw_id}/export?format=zwo")
    assert r_zwo.status_code == 200
    assert "<workout_file>" in r_zwo.text

    # 3. Upload d'un FIT le 2026-11-19 => appariement automatique !
    fit_bytes = _make_fit_on_date(datetime(2026, 11, 19, 9, 0, 0, tzinfo=UTC), duration_s=300)
    r_up = await client.post(
        "/v1/orgs/org-p2/activities/upload",
        data={"athlete_uid": "ath-p2"},
        files={"file": ("sortie.fit", io.BytesIO(fit_bytes), "application/octet-stream")},
    )
    assert r_up.status_code == 201

    # 4. Objectif A + Calendrier semaine
    r_goal = await client.post(
        "/v1/orgs/org-p2/goals",
        json={"uid": "ath-p2", "title": "Cyclosportive", "date": "2026-11-22", "sport": "cyclisme", "priority": "A"},
    )
    assert r_goal.status_code == 201

    r_cal = await client.get("/v1/orgs/org-p2/calendar?from=2026-11-16&to=2026-11-22&uid=ath-p2")
    assert r_cal.status_code == 200
    cal = r_cal.json()
    assert len(cal["planned_workouts"]) == 1
    assert cal["planned_workouts"][0]["status"] == "done"
    assert cal["planned_workouts"][0]["compliance"]["color"] == "green"
    assert len(cal["activities"]) == 1
    assert len(cal["goals"]) == 1
    assert len(cal["weekly_totals"]) == 1

    # 5. Plan multi-semaines appliqué au groupe
    r_plan = await client.post(
        "/v1/orgs/org-p2/plans",
        json={
            "name": "Bloc 2 semaines",
            "sport": "cyclisme",
            "weeks": 2,
            "items": [
                {"day_offset": 0, "title": "S1 Lundi", "sport": "cyclisme", "steps": [{"kind": "active", "duration_s": 1800, "target_type": "ftp_pct", "target_min": 70, "target_max": 70}]},
                {"day_offset": 7, "title": "S2 Lundi", "sport": "cyclisme", "steps": [{"kind": "active", "duration_s": 2400, "target_type": "ftp_pct", "target_min": 75, "target_max": 75}]},
            ],
        },
    )
    assert r_plan.status_code == 201
    plan_id = r_plan.json()["plan"]["id"]

    r_apply = await client.post(
        f"/v1/orgs/org-p2/plans/{plan_id}/apply",
        json={"group_id": "grp-p2", "start_date": "2026-12-01"},
    )
    assert r_apply.status_code == 201
    assert r_apply.json()["created_count"] == 2

    # 6. RBAC : l'athlète ne peut pas créer un modèle de bibliothèque
    mock_claims["uid"] = "ath-p2"
    r_forbid = await client.post(
        "/v1/orgs/org-p2/workouts/templates",
        json={"name": "Interdit", "sport": "cyclisme", "steps": []},
    )
    assert r_forbid.status_code == 403
