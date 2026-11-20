"""Tests Phase 3 : moteur PMC (CTL/ATL/TSB/ramp rate), Mean-Max, Wellness RGPD et Coach Overview."""
from datetime import UTC, date, datetime, timedelta

import pytest

import db as db_mod
from services.pmc import compute_pmc_series


def test_compute_pmc_series_math():
    start = date(2026, 10, 1)
    end = start + timedelta(days=41)  # 42 jours à 100 TSS/j
    tss_map = {(start + timedelta(days=i)).isoformat(): 100.0 for i in range(42)}

    series = compute_pmc_series(tss_map, start, end, today=end)
    assert len(series) == 42
    last = series[-1]
    # 100 * (1 - (41/42)^42) ≈ 63.6 ; ATL ≈ 99.8 ; TSB < 0
    assert 62.0 <= last["ctl"] <= 65.0
    assert 99.0 <= last["atl"] <= 100.0
    assert last["tsb"] < -30.0
    assert last["ramp_rate"] > 0
    assert last["is_projected"] is False


@pytest.mark.asyncio
async def test_pmc_wellness_rgpd_and_coach_overview(client, mock_claims):
    await db_mod.users_col.insert_many(
        [
            {"_id": "coach-p3", "email": "coach@p3.fr", "first_name": "Coach", "last_name": "P3"},
            {"_id": "ath-p3", "email": "ath@p3.fr", "first_name": "Lucas", "last_name": "Tri", "health_consent": {"granted": False}},
        ]
    )
    await db_mod.orgs_col.insert_one(
        {"_id": "org-p3", "type": "club", "name": "Club P3", "owner_uid": "coach-p3", "status": "active"}
    )
    await db_mod.memberships_col.insert_many(
        [
            {"org_id": "org-p3", "uid": "coach-p3", "role": "coach", "status": "active", "group_ids": []},
            {"org_id": "org-p3", "uid": "ath-p3", "role": "athlete", "status": "active", "group_ids": []},
        ]
    )

    today = datetime.now(UTC).date()
    # Semer une grosse charge sur les 7 derniers jours pour déclencher le drapeau overload
    acts = []
    for i in range(7):
        d = datetime.combine(today - timedelta(days=i), datetime.min.time(), tzinfo=UTC)
        acts.append(
            {
                "_id": f"act-p3-{i}",
                "org_id": "org-p3",
                "uid": "ath-p3",
                "sport": "cyclisme",
                "title": f"Sortie {i}",
                "start_time": d,
                "duration_s": 7200,
                "distance_m": 60000,
                "metrics": {
                    "tss": 180.0,
                    "mean_max_power": {"60": 350.0 + i * 5, "1200": 270.0},
                    "mean_max_hr": {"60": 182.0},
                },
            }
        )
    await db_mod.activities_col.insert_many(acts)

    # 1. GET /analytics/pmc calcule et retourne la série + projection
    mock_claims["uid"] = "coach-p3"
    r_pmc = await client.get("/v1/orgs/org-p3/analytics/pmc?uid=ath-p3&include_projected=true")
    assert r_pmc.status_code == 200
    items = r_pmc.json()["items"]
    assert any(row["is_projected"] is True for row in items)
    today_row = next(r for r in items if r["date"] == today.isoformat())
    assert today_row["tsb"] < -25.0

    # 2. Mean-Max agrégé
    r_mm = await client.get("/v1/orgs/org-p3/analytics/mean-max?uid=ath-p3")
    assert r_mm.status_code == 200
    assert r_mm.json()["power"]["60"] == 380.0

    # 3. Wellness RGPD : hrv_ms refusé en 422 si pas de consentement santé
    mock_claims["uid"] = "ath-p3"
    r_well_422 = await client.post(
        "/v1/orgs/org-p3/wellness",
        json={"date": today.isoformat(), "fatigue": 4, "hrv_ms": 68.0},
    )
    assert r_well_422.status_code == 422

    # Sans données physio (uniquement subjectif), accepté en 201
    r_well_ok = await client.post(
        "/v1/orgs/org-p3/wellness",
        json={"date": today.isoformat(), "sleep_h": 6.5, "sleep_quality": 2, "fatigue": 5},
    )
    assert r_well_ok.status_code == 201

    # 4. Coach overview remonte overload + high_fatigue
    mock_claims["uid"] = "coach-p3"
    r_ov = await client.get("/v1/orgs/org-p3/coach/overview")
    assert r_ov.status_code == 200
    ath_row = r_ov.json()["athletes"][0]
    assert "overload" in ath_row["flags"]
    assert "high_fatigue" in ath_row["flags"]
    assert ath_row["tss_7d"] == 1260.0


@pytest.mark.asyncio
async def test_analytics_athletes_only_filtering(client, mock_claims):
    """Les routes analytics filtrent par défaut sur les membres ayant le rôle athlete."""
    await db_mod.users_col.insert_many(
        [
            {"_id": "coach-flt", "email": "coach@flt.fr", "first_name": "Coach", "last_name": "Flt"},
            {"_id": "ath-flt", "email": "ath@flt.fr", "first_name": "Ath", "last_name": "Flt"},
        ]
    )
    await db_mod.orgs_col.insert_one(
        {"_id": "org-flt", "type": "club", "name": "Club Flt", "owner_uid": "coach-flt", "status": "active"}
    )
    await db_mod.memberships_col.insert_many(
        [
            {"org_id": "org-flt", "uid": "coach-flt", "role": "coach", "status": "active", "group_ids": []},
            {"org_id": "org-flt", "uid": "ath-flt", "role": "athlete", "status": "active", "group_ids": []},
        ]
    )

    mock_claims.update({"uid": "coach-flt", "email": "coach@flt.fr"})

    # Sans uid, le coach résout automatiquement vers le premier athlète (pas lui-même)
    r_default = await client.get("/v1/orgs/org-flt/analytics/pmc")
    assert r_default.status_code == 200
    assert r_default.json()["uid"] == "ath-flt"

    # Cibler explicitement un coach avec athletes_only=true (défaut) -> 404
    r_coach_target = await client.get("/v1/orgs/org-flt/analytics/pmc?uid=coach-flt")
    assert r_coach_target.status_code == 404

    # Organisation sans athlète -> réponse vide propre (uid=None)
    await db_mod.orgs_col.insert_one(
        {"_id": "org-empty", "type": "solo_coach", "name": "Solo Vide", "owner_uid": "coach-flt", "status": "active"}
    )
    await db_mod.memberships_col.insert_one(
        {"org_id": "org-empty", "uid": "coach-flt", "role": "owner", "status": "active", "group_ids": []}
    )
    r_empty = await client.get("/v1/orgs/org-empty/analytics/pmc")
    assert r_empty.status_code == 200
    assert r_empty.json() == {"uid": None, "items": [], "goals": []}


@pytest.mark.asyncio
async def test_athlete_report_pdf_export_and_batch_kpis(client, mock_claims):
    """Vérifie l'export PDF du bilan athlète et les KPIs batch anti-N+1."""
    now = datetime.now(UTC)
    today = now.date()
    yesterday = today - timedelta(days=1)

    await db_mod.orgs_col.insert_one(
        {
            "_id": "org-pdf",
            "type": "club",
            "name": "Club Vélo Élite — Lyon",
            "owner_uid": "coach-pdf",
            "status": "active",
        }
    )
    await db_mod.users_col.insert_many(
        [
            {"_id": "coach-pdf", "email": "coach@pdf.fr", "first_name": "Claire", "last_name": "Coach"},
            {
                "_id": "ath-pdf-1",
                "email": "eleve1@pdf.fr",
                "first_name": "Éléonore",
                "last_name": "Cœur",
                "athlete_profile": {"sport": "home_trainer", "level": "nationale", "birth_date": "1996-05-10"},
            },
            {
                "_id": "ath-pdf-2",
                "email": "eleve2@pdf.fr",
                "first_name": "Lucas",
                "last_name": "Test",
            },
        ]
    )
    await db_mod.memberships_col.insert_many(
        [
            {"org_id": "org-pdf", "uid": "coach-pdf", "role": "owner", "status": "active", "group_ids": []},
            {"org_id": "org-pdf", "uid": "ath-pdf-1", "role": "athlete", "status": "active", "group_ids": []},
            {"org_id": "org-pdf", "uid": "ath-pdf-2", "role": "athlete", "status": "active", "group_ids": []},
        ]
    )
    await db_mod.activities_col.insert_many(
        [
            {
                "_id": "act-pdf-1",
                "org_id": "org-pdf",
                "uid": "ath-pdf-1",
                "sport": "home_trainer",
                "title": "Sweet Spot 2x20",
                "start_time": now - timedelta(days=2),
                "duration_s": 3600,
                "distance_m": 32500,
                "metrics": {"tss": 82.0, "mean_max_power": {"5": 780, "60": 420, "300": 310, "1200": 275}},
            },
            {
                "_id": "act-pdf-2",
                "org_id": "org-pdf",
                "uid": "ath-pdf-1",
                "sport": "cyclisme",
                "title": "Sortie longue",
                "start_time": now - timedelta(days=1),
                "duration_s": 7200,
                "distance_m": 64000,
                "metrics": {"tss": 120.0, "mean_max_power": {"5": 820, "1200": 265}},
            },
            {
                "_id": "act-pdf-3",
                "org_id": "org-pdf",
                "uid": "ath-pdf-2",
                "sport": "course à pied",
                "title": "Footing",
                "start_time": now - timedelta(days=1),
                "duration_s": 2400,
                "distance_m": 8000,
                "metrics": {"tss": 45.0},
            },
        ]
    )
    await db_mod.daily_metrics_col.insert_many(
        [
            {"org_id": "org-pdf", "uid": "ath-pdf-1", "date": (today - timedelta(days=10)).isoformat(), "ctl": 40.0, "atl": 42.0, "tsb": -2.0, "ramp_rate": 2.0},
            {"org_id": "org-pdf", "uid": "ath-pdf-1", "date": today.isoformat(), "ctl": 48.5, "atl": 55.0, "tsb": -6.5, "ramp_rate": 4.2},
            {"org_id": "org-pdf", "uid": "ath-pdf-2", "date": today.isoformat(), "ctl": 30.0, "atl": 60.0, "tsb": -30.0, "ramp_rate": 9.5},
        ]
    )
    await db_mod.planned_workouts_col.insert_many(
        [
            {"_id": "pw-1", "org_id": "org-pdf", "uid": "ath-pdf-1", "date": yesterday.isoformat(), "status": "done", "activity_id": "act-pdf-2"},
            {"_id": "pw-2", "org_id": "org-pdf", "uid": "ath-pdf-2", "date": yesterday.isoformat(), "status": "planned", "activity_id": None},
        ]
    )
    await db_mod.wellness_entries_col.insert_one(
        {"org_id": "org-pdf", "uid": "ath-pdf-1", "date": today.isoformat(), "sleep_h": 7.8, "fatigue": 2, "sleep_quality": 4}
    )

    mock_claims.update({"uid": "coach-pdf", "email": "coach@pdf.fr"})

    # 1. Batch coach/overview
    r_ov = await client.get("/v1/orgs/org-pdf/coach/overview")
    assert r_ov.status_code == 200
    ov_by_uid = {a["uid"]: a for a in r_ov.json()["athletes"]}
    assert ov_by_uid["ath-pdf-1"]["tss_7d"] == 202.0
    assert ov_by_uid["ath-pdf-1"]["missed_workouts_7d"] == 0
    assert "overload" in ov_by_uid["ath-pdf-2"]["flags"]
    assert "missed_workouts" in ov_by_uid["ath-pdf-2"]["flags"]

    # 2. Batch athletes-kpis
    r_kpis = await client.get("/v1/orgs/org-pdf/analytics/athletes-kpis")
    assert r_kpis.status_code == 200
    assert r_kpis.json()["team"]["athletes_count"] == 2
    assert r_kpis.json()["team"]["total_tss_7d"] == 247.0

    # 3. Rapport JSON + by_sport
    r_json = await client.get("/v1/orgs/org-pdf/analytics/report?uid=ath-pdf-1&days=30")
    assert r_json.status_code == 200
    rep = r_json.json()
    assert rep["activities"]["count"] == 2
    assert len(rep["by_sport"]) == 2
    assert rep["records_power_w"]["5s"] == 820

    # 4. Export PDF valide
    r_pdf = await client.get("/v1/orgs/org-pdf/analytics/report/pdf?uid=ath-pdf-1&days=30")
    assert r_pdf.status_code == 200
    assert r_pdf.headers["content-type"] == "application/pdf"
    assert "attachment;" in r_pdf.headers.get("content-disposition", "")
    assert r_pdf.content.startswith(b"%PDF-")
    assert len(r_pdf.content) > 1500


@pytest.mark.asyncio
async def test_lobby_returns_personalized_participant_thresholds(client, mock_claims):
    """Vérifie que /rooms/{room}/lobby expose lthr_bpm et hr_max par participant."""
    await db_mod.orgs_col.insert_one(
        {"_id": "org-hr", "type": "club", "name": "Club HR", "owner_uid": "coach-hr", "status": "active"}
    )
    await db_mod.users_col.insert_many(
        [
            {"_id": "coach-hr", "email": "c@hr.fr", "first_name": "Coach", "last_name": "One"},
            {"_id": "ath-hr-1", "email": "a1@hr.fr", "first_name": "Alice", "last_name": "LTHR"},
            {
                "_id": "ath-hr-2",
                "email": "a2@hr.fr",
                "first_name": "Bob",
                "last_name": "Max",
                "health_consent": {"granted": True},
                "athlete_profile": {"sport": "cyclisme", "level": "loisir", "hr_max": 194, "hr_rest": 50},
            },
        ]
    )
    await db_mod.memberships_col.insert_many(
        [
            {"org_id": "org-hr", "uid": "coach-hr", "role": "owner", "status": "active", "group_ids": []},
            {"org_id": "org-hr", "uid": "ath-hr-1", "role": "athlete", "status": "active", "group_ids": []},
            {"org_id": "org-hr", "uid": "ath-hr-2", "role": "athlete", "status": "active", "group_ids": []},
        ]
    )
    await db_mod.threshold_history_col.insert_one(
        {
            "_id": "thr-1",
            "org_id": "org-hr",
            "uid": "ath-hr-1",
            "sport": "home_trainer",
            "effective_from": "2026-11-01",
            "ftp_w": 290,
            "lthr_bpm": 172,
            "hr_max": 190,
        }
    )
    await db_mod.rooms.insert_one(
        {
            "room_name": "room-hr-zones",
            "org_id": "org-hr",
            "owner_uid": "coach-hr",
            "title": "Test Zones FC",
            "participant_uids": ["coach-hr", "ath-hr-1", "ath-hr-2"],
            "count": 0,
            "finished": False,
        }
    )

    mock_claims.update({"uid": "coach-hr", "email": "c@hr.fr"})
    res = await client.get("/rooms/room-hr-zones/lobby")
    assert res.status_code == 200
    thr_map = res.json()["participant_thresholds"]
    assert thr_map["ath-hr-1"]["lthr_bpm"] == 172
    assert thr_map["ath-hr-1"]["hr_max"] == 190
    assert thr_map["ath-hr-1"]["ftp_w"] == 290
    assert thr_map["ath-hr-2"]["hr_max"] == 194
    assert thr_map["ath-hr-2"]["hr_rest"] == 50
