"""Routes /v1/orgs/{org_id}/analytics, /wellness et /coach/overview."""
from datetime import UTC, datetime, timedelta
import re
from typing import Any
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from auth_firebase import OrgContext, require_org_role
import db as db_mod
from models import WellnessEntryIn
from services.pmc import recompute_pmc
from services.report_pdf import render_athlete_report_pdf

router = APIRouter(prefix="/v1/orgs/{org_id}", tags=["analytics"])


async def _resolve_target_uid(
    org_id: str,
    uid: str | None,
    ctx: OrgContext,
    *,
    athletes_only: bool = True,
) -> str | None:
    """Résout l'utilisateur cible des KPIs/graphes en restreignant par défaut aux athlètes."""
    if ctx.role == "athlete":
        return ctx.account.uid
    if uid:
        query: dict[str, Any] = {"org_id": org_id, "uid": uid, "status": "active"}
        if athletes_only:
            query["role"] = "athlete"
        member = await db_mod.memberships_col.find_one(query, {"_id": 1})
        if not member:
            raise HTTPException(
                status_code=404,
                detail="Athlète introuvable dans cette organisation."
                if athletes_only
                else "Membre introuvable dans cette organisation.",
            )
        return uid
    if athletes_only:
        first_athlete = await db_mod.memberships_col.find_one(
            {"org_id": org_id, "status": "active", "role": "athlete"},
            {"uid": 1},
            sort=[("created_at", 1)],
        )
        return first_athlete["uid"] if first_athlete else None
    return ctx.account.uid


@router.get("/analytics/pmc")
async def get_pmc(
    org_id: str,
    uid: str | None = Query(default=None),
    from_date: str | None = Query(default=None, alias="from"),
    to_date: str | None = Query(default=None, alias="to"),
    include_projected: bool = Query(default=True),
    athletes_only: bool = Query(default=True),
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach", "athlete")),
):
    target_uid = await _resolve_target_uid(org_id, uid, ctx, athletes_only=athletes_only)
    if not target_uid:
        return {"uid": None, "items": [], "goals": []}

    today = datetime.now(UTC).date()
    from_iso = from_date or (today - timedelta(days=90)).isoformat()
    to_iso = to_date or (
        (today + timedelta(days=28)).isoformat() if include_projected else today.isoformat()
    )

    docs = (
        await db_mod.daily_metrics_col.find(
            {
                "org_id": org_id,
                "uid": target_uid,
                "date": {"$gte": from_iso, "$lte": to_iso},
            },
            {"_id": 0, "org_id": 0, "uid": 0, "updated_at": 0},
        )
        .sort("date", 1)
        .to_list(1000)
    )
    if not docs:
        series = await recompute_pmc(org_id, target_uid)
        docs = [r for r in series if from_iso <= r["date"] <= to_iso]

    if not include_projected:
        docs = [d for d in docs if not d.get("is_projected")]

    goals = (
        await db_mod.goals_col.find(
            {
                "org_id": org_id,
                "uid": target_uid,
                "date": {"$gte": from_iso, "$lte": to_iso},
            },
            {"_id": 0, "title": 1, "date": 1, "priority": 1, "sport": 1},
        )
        .sort("date", 1)
        .to_list(50)
    )

    return {"uid": target_uid, "items": docs, "goals": goals}


@router.get("/analytics/mean-max")
async def get_mean_max(
    org_id: str,
    uid: str | None = Query(default=None),
    sport: str | None = Query(default=None),
    from_date: str | None = Query(default=None, alias="from"),
    to_date: str | None = Query(default=None, alias="to"),
    athletes_only: bool = Query(default=True),
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach", "athlete")),
):
    target_uid = await _resolve_target_uid(org_id, uid, ctx, athletes_only=athletes_only)
    if not target_uid:
        return {"uid": None, "power": {}, "hr": {}}
    query: dict[str, Any] = {"org_id": org_id, "uid": target_uid}
    if sport:
        query["sport"] = sport
    if from_date or to_date:
        dt_range: dict[str, datetime] = {}
        if from_date:
            dt_range["$gte"] = datetime.fromisoformat(f"{from_date}T00:00:00+00:00")
        if to_date:
            dt_range["$lte"] = datetime.fromisoformat(f"{to_date}T23:59:59+00:00")
        query["start_time"] = dt_range

    best_power: dict[str, float] = {}
    best_hr: dict[str, float] = {}

    async for act in db_mod.activities_col.find(
        query, {"metrics.mean_max_power": 1, "metrics.mean_max_hr": 1}
    ):
        m = act.get("metrics") or {}
        for dur, val in (m.get("mean_max_power") or {}).items():
            if dur not in best_power or float(val) > best_power[dur]:
                best_power[dur] = float(val)
        for dur, val in (m.get("mean_max_hr") or {}).items():
            if dur not in best_hr or float(val) > best_hr[dur]:
                best_hr[dur] = float(val)

    return {
        "uid": target_uid,
        "power": dict(sorted(best_power.items(), key=lambda kv: int(kv[0]))),
        "hr": dict(sorted(best_hr.items(), key=lambda kv: int(kv[0]))),
    }


@router.get("/wellness")
async def list_wellness(
    org_id: str,
    uid: str | None = Query(default=None),
    from_date: str | None = Query(default=None, alias="from"),
    to_date: str | None = Query(default=None, alias="to"),
    athletes_only: bool = Query(default=True),
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach", "athlete")),
):
    target_uid = await _resolve_target_uid(org_id, uid, ctx, athletes_only=athletes_only)
    if not target_uid:
        return {"uid": None, "items": []}
    today = datetime.now(UTC).date()
    from_iso = from_date or (today - timedelta(days=30)).isoformat()
    to_iso = to_date or today.isoformat()

    docs = (
        await db_mod.wellness_entries_col.find(
            {
                "org_id": org_id,
                "uid": target_uid,
                "date": {"$gte": from_iso, "$lte": to_iso},
            },
            {"_id": 0},
        )
        .sort("date", -1)
        .to_list(100)
    )
    return {"uid": target_uid, "items": docs}


@router.post("/wellness", status_code=201)
async def upsert_wellness(
    org_id: str,
    body: WellnessEntryIn,
    uid: str | None = Query(default=None),
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach", "athlete")),
):
    target_uid = ctx.account.uid if ctx.role == "athlete" else (uid or ctx.account.uid)

    has_physio = any(
        v is not None for v in (body.hrv_ms, body.resting_hr, body.weight_kg)
    )
    if has_physio:
        user = await db_mod.users_col.find_one(
            {"_id": target_uid}, {"health_consent": 1}
        )
        consent = (user or {}).get("health_consent") or {}
        if consent.get("granted") is not True:
            raise HTTPException(
                status_code=422,
                detail="Le consentement explicite aux données de santé est requis pour enregistrer la VFC, la FC de repos ou le poids.",
            )

    d_iso = body.entry_date.isoformat()
    now = datetime.now(UTC)
    payload = {
        "org_id": org_id,
        "uid": target_uid,
        "date": d_iso,
        "sleep_h": body.sleep_h,
        "sleep_quality": body.sleep_quality,
        "fatigue": body.fatigue,
        "soreness": body.soreness,
        "stress": body.stress,
        "mood": body.mood,
        "hrv_ms": body.hrv_ms,
        "resting_hr": body.resting_hr,
        "weight_kg": body.weight_kg,
        "notes": body.notes,
        "updated_at": now,
    }
    await db_mod.wellness_entries_col.update_one(
        {"org_id": org_id, "uid": target_uid, "date": d_iso},
        {"$set": payload, "$setOnInsert": {"_id": uuid.uuid4().hex, "created_at": now}},
        upsert=True,
    )
    saved = await db_mod.wellness_entries_col.find_one(
        {"org_id": org_id, "uid": target_uid, "date": d_iso}, {"_id": 0}
    )
    return {"entry": saved}


@router.get("/coach/overview")
async def coach_overview(
    org_id: str,
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach")),
):
    today = datetime.now(UTC).date()
    today_iso = today.isoformat()
    week_ago_iso = (today - timedelta(days=7)).isoformat()
    week_ago_dt = datetime.fromisoformat(f"{week_ago_iso}T00:00:00+00:00")

    memberships = await db_mod.memberships_col.find(
        {"org_id": org_id, "status": "active", "role": "athlete"},
        {"uid": 1, "group_ids": 1},
    ).to_list(200)
    uids = [m["uid"] for m in memberships]
    if not uids:
        return {"athletes": []}

    users_map: dict[str, str] = {}
    async for u in db_mod.users_col.find(
        {"_id": {"$in": uids}}, {"first_name": 1, "last_name": 1, "email": 1}
    ):
        name = f"{u.get('first_name') or ''} {u.get('last_name') or ''}".strip()
        users_map[u["_id"]] = name or u.get("email") or u["_id"]

    # Requêtes batch (anti N+1) : 4 requêtes au lieu de 4 * N
    latest_pmc_by_uid: dict[str, dict[str, Any]] = {}
    async for pmc in db_mod.daily_metrics_col.find(
        {"org_id": org_id, "uid": {"$in": uids}, "date": {"$lte": today_iso}},
        {"_id": 0},
    ).sort("date", -1):
        p_uid = pmc.get("uid")
        if p_uid and p_uid not in latest_pmc_by_uid:
            latest_pmc_by_uid[p_uid] = pmc

    tss_7d_by_uid: dict[str, float] = {}
    async for a in db_mod.activities_col.find(
        {"org_id": org_id, "uid": {"$in": uids}, "start_time": {"$gte": week_ago_dt}},
        {"uid": 1, "metrics.tss": 1},
    ):
        a_uid = a.get("uid")
        if a_uid:
            tss_7d_by_uid[a_uid] = tss_7d_by_uid.get(a_uid, 0.0) + float(
                (a.get("metrics") or {}).get("tss") or 0.0
            )

    latest_well_by_uid: dict[str, dict[str, Any]] = {}
    async for w in db_mod.wellness_entries_col.find(
        {"org_id": org_id, "uid": {"$in": uids}},
        {"_id": 0},
    ).sort("date", -1):
        w_uid = w.get("uid")
        if w_uid and w_uid not in latest_well_by_uid:
            latest_well_by_uid[w_uid] = w

    missed_by_uid: dict[str, int] = {}
    async for pw in db_mod.planned_workouts_col.find(
        {
            "org_id": org_id,
            "uid": {"$in": uids},
            "date": {"$gte": week_ago_iso, "$lt": today_iso},
            "activity_id": None,
            "status": {"$in": ["planned", "missed"]},
        },
        {"uid": 1},
    ):
        pw_uid = pw.get("uid")
        if pw_uid:
            missed_by_uid[pw_uid] = missed_by_uid.get(pw_uid, 0) + 1

    athletes_out = []
    for uid in uids:
        latest_pmc = latest_pmc_by_uid.get(uid)
        ctl = latest_pmc.get("ctl", 0.0) if latest_pmc else 0.0
        atl = latest_pmc.get("atl", 0.0) if latest_pmc else 0.0
        tsb = latest_pmc.get("tsb", 0.0) if latest_pmc else 0.0
        ramp_rate = latest_pmc.get("ramp_rate", 0.0) if latest_pmc else 0.0

        tss_7d = tss_7d_by_uid.get(uid, 0.0)
        latest_well = latest_well_by_uid.get(uid)
        missed_count = missed_by_uid.get(uid, 0)

        flags: list[str] = []
        if tsb < -25:
            flags.append("overload")
        if ramp_rate > 8:
            flags.append("high_ramp")
        if latest_well and (
            (latest_well.get("fatigue") or 0) >= 4
            or (latest_well.get("sleep_quality") or 5) <= 2
        ):
            flags.append("high_fatigue")
        if missed_count > 0:
            flags.append("missed_workouts")

        athletes_out.append(
            {
                "uid": uid,
                "name": users_map.get(uid, uid),
                "ctl": ctl,
                "atl": atl,
                "tsb": tsb,
                "ramp_rate": ramp_rate,
                "tss_7d": round(tss_7d, 1),
                "latest_wellness": latest_well,
                "missed_workouts_7d": missed_count,
                "flags": flags,
            }
        )

    return {"athletes": athletes_out}


@router.get("/analytics/athletes-kpis")
async def get_athletes_kpis(
    org_id: str,
    group_id: str | None = Query(default=None),
    uids: str | None = Query(default=None),
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach")),
):
    """P1 : KPIs comparatifs par athlète et agrégats d'équipe (athlètes uniquement)."""
    today = datetime.now(UTC).date()
    today_iso = today.isoformat()
    week_ago_dt = datetime.fromisoformat(f"{(today - timedelta(days=7)).isoformat()}T00:00:00+00:00")
    month_ago_iso = (today - timedelta(days=28)).isoformat()
    month_ago_dt = datetime.fromisoformat(f"{month_ago_iso}T00:00:00+00:00")

    member_query: dict[str, Any] = {"org_id": org_id, "status": "active", "role": "athlete"}
    if group_id:
        member_query["group_ids"] = group_id
    if uids:
        uid_list = [u.strip() for u in uids.split(",") if u.strip()]
        if uid_list:
            member_query["uid"] = {"$in": uid_list}

    memberships = await db_mod.memberships_col.find(member_query, {"uid": 1}).to_list(200)
    target_uids = [m["uid"] for m in memberships]
    if not target_uids:
        return {
            "athletes": [],
            "team": {
                "athletes_count": 0,
                "avg_ctl": 0.0,
                "total_tss_7d": 0.0,
                "avg_compliance_pct": 0.0,
            },
        }

    users_map: dict[str, str] = {}
    async for u in db_mod.users_col.find(
        {"_id": {"$in": target_uids}}, {"first_name": 1, "last_name": 1, "email": 1}
    ):
        name = f"{u.get('first_name') or ''} {u.get('last_name') or ''}".strip()
        users_map[u["_id"]] = name or u.get("email") or u["_id"]

    # Requêtes batch (anti N+1) : 4 requêtes au lieu de 5 * N
    latest_pmc_by_uid: dict[str, dict[str, Any]] = {}
    async for pmc in db_mod.daily_metrics_col.find(
        {"org_id": org_id, "uid": {"$in": target_uids}, "date": {"$lte": today_iso}},
        {"_id": 0},
    ).sort("date", -1):
        p_uid = pmc.get("uid")
        if p_uid and p_uid not in latest_pmc_by_uid:
            latest_pmc_by_uid[p_uid] = pmc

    tss_7d_by_uid: dict[str, float] = {}
    tss_28d_by_uid: dict[str, float] = {}
    acts_7d_by_uid: dict[str, int] = {}
    async for a in db_mod.activities_col.find(
        {"org_id": org_id, "uid": {"$in": target_uids}, "start_time": {"$gte": month_ago_dt}},
        {"uid": 1, "start_time": 1, "metrics.tss": 1},
    ):
        a_uid = a.get("uid")
        if not a_uid:
            continue
        tss_val = float((a.get("metrics") or {}).get("tss") or 0.0)
        tss_28d_by_uid[a_uid] = tss_28d_by_uid.get(a_uid, 0.0) + tss_val
        st = a.get("start_time")
        if isinstance(st, datetime):
            st_utc = st if st.tzinfo else st.replace(tzinfo=UTC)
            if st_utc >= week_ago_dt:
                tss_7d_by_uid[a_uid] = tss_7d_by_uid.get(a_uid, 0.0) + tss_val
                acts_7d_by_uid[a_uid] = acts_7d_by_uid.get(a_uid, 0) + 1

    planned_total_by_uid: dict[str, int] = {}
    planned_done_by_uid: dict[str, int] = {}
    async for pw in db_mod.planned_workouts_col.find(
        {
            "org_id": org_id,
            "uid": {"$in": target_uids},
            "date": {"$gte": month_ago_iso, "$lte": today_iso},
        },
        {"uid": 1, "activity_id": 1},
    ):
        pw_uid = pw.get("uid")
        if not pw_uid:
            continue
        planned_total_by_uid[pw_uid] = planned_total_by_uid.get(pw_uid, 0) + 1
        if pw.get("activity_id") is not None:
            planned_done_by_uid[pw_uid] = planned_done_by_uid.get(pw_uid, 0) + 1

    latest_well_by_uid: dict[str, dict[str, Any]] = {}
    async for w in db_mod.wellness_entries_col.find(
        {"org_id": org_id, "uid": {"$in": target_uids}}, {"_id": 0}
    ).sort("date", -1):
        w_uid = w.get("uid")
        if w_uid and w_uid not in latest_well_by_uid:
            latest_well_by_uid[w_uid] = w

    athletes_rows: list[dict[str, Any]] = []
    for uid in target_uids:
        latest_pmc = latest_pmc_by_uid.get(uid)
        ctl = round(float(latest_pmc.get("ctl", 0.0)), 1) if latest_pmc else 0.0
        atl = round(float(latest_pmc.get("atl", 0.0)), 1) if latest_pmc else 0.0
        tsb = round(float(latest_pmc.get("tsb", 0.0)), 1) if latest_pmc else 0.0

        planned_total = planned_total_by_uid.get(uid, 0)
        planned_done = planned_done_by_uid.get(uid, 0)
        compliance_pct = (
            round((planned_done / planned_total) * 100, 1) if planned_total > 0 else 100.0
        )

        athletes_rows.append(
            {
                "uid": uid,
                "name": users_map.get(uid, uid),
                "ctl": ctl,
                "atl": atl,
                "tsb": tsb,
                "tss_7d": round(tss_7d_by_uid.get(uid, 0.0), 1),
                "tss_28d": round(tss_28d_by_uid.get(uid, 0.0), 1),
                "activities_count_7d": acts_7d_by_uid.get(uid, 0),
                "compliance_pct_28d": compliance_pct,
                "latest_wellness": latest_well_by_uid.get(uid),
            }
        )

    n = len(athletes_rows)
    return {
        "athletes": athletes_rows,
        "team": {
            "athletes_count": n,
            "avg_ctl": round(sum(r["ctl"] for r in athletes_rows) / n, 1),
            "total_tss_7d": round(sum(r["tss_7d"] for r in athletes_rows), 1),
            "avg_compliance_pct": round(sum(r["compliance_pct_28d"] for r in athletes_rows) / n, 1),
        },
    }


async def _compute_athlete_report(
    org_id: str,
    uid: str | None,
    days: int,
    ctx: OrgContext,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    target_uid = await _resolve_target_uid(org_id, uid, ctx, athletes_only=True)
    if not target_uid:
        raise HTTPException(status_code=404, detail="Aucun athlète disponible pour le bilan.")

    today = datetime.now(UTC).date()
    from_iso = (today - timedelta(days=days)).isoformat()
    to_iso = today.isoformat()
    from_dt = datetime.fromisoformat(f"{from_iso}T00:00:00+00:00")

    user = await db_mod.users_col.find_one(
        {"_id": target_uid}, {"first_name": 1, "last_name": 1, "email": 1, "athlete_profile": 1}
    )
    athlete_name = (
        f"{(user or {}).get('first_name') or ''} {(user or {}).get('last_name') or ''}".strip()
        or (user or {}).get("email")
        or target_uid
    )

    act_count = 0
    total_duration_s = 0.0
    total_distance_m = 0.0
    total_tss = 0.0
    best_5s = 0.0
    best_60s = 0.0
    best_300s = 0.0
    best_1200s = 0.0
    sport_buckets: dict[str, dict[str, Any]] = {}

    async for a in db_mod.activities_col.find(
        {"org_id": org_id, "uid": target_uid, "start_time": {"$gte": from_dt}}
    ):
        act_count += 1
        dur = float(a.get("duration_s") or 0)
        dist = float(a.get("distance_m") or 0)
        m = a.get("metrics") or {}
        tss = float(m.get("tss") or 0)
        total_duration_s += dur
        total_distance_m += dist
        total_tss += tss

        sp = a.get("sport") or "autre"
        bucket = sport_buckets.setdefault(
            sp, {"sport": sp, "count": 0, "duration_s": 0.0, "distance_m": 0.0, "tss": 0.0}
        )
        bucket["count"] += 1
        bucket["duration_s"] += dur
        bucket["distance_m"] += dist
        bucket["tss"] += tss

        mmp = m.get("mean_max_power") or {}
        best_5s = max(best_5s, float(mmp.get("5") or 0))
        best_60s = max(best_60s, float(mmp.get("60") or 0))
        best_300s = max(best_300s, float(mmp.get("300") or 0))
        best_1200s = max(best_1200s, float(mmp.get("1200") or 0))

    by_sport = [
        {
            "sport": b["sport"],
            "count": b["count"],
            "duration_s": round(b["duration_s"]),
            "distance_km": round(b["distance_m"] / 1000.0, 2),
            "tss": round(b["tss"], 1),
        }
        for b in sorted(sport_buckets.values(), key=lambda x: x["duration_s"], reverse=True)
    ]

    pmc_docs = (
        await db_mod.daily_metrics_col.find(
            {"org_id": org_id, "uid": target_uid, "date": {"$gte": from_iso, "$lte": to_iso}},
            {"_id": 0, "date": 1, "ctl": 1, "atl": 1, "tsb": 1},
        )
        .sort("date", 1)
        .to_list(400)
    )
    pmc_start = pmc_docs[0] if pmc_docs else None
    pmc_end = pmc_docs[-1] if pmc_docs else None

    planned_total = await db_mod.planned_workouts_col.count_documents(
        {"org_id": org_id, "uid": target_uid, "date": {"$gte": from_iso, "$lte": to_iso}}
    )
    planned_done = await db_mod.planned_workouts_col.count_documents(
        {
            "org_id": org_id,
            "uid": target_uid,
            "date": {"$gte": from_iso, "$lte": to_iso},
            "activity_id": {"$ne": None},
        }
    )

    well_docs = await db_mod.wellness_entries_col.find(
        {"org_id": org_id, "uid": target_uid, "date": {"$gte": from_iso, "$lte": to_iso}}
    ).to_list(400)
    sleep_vals = [float(w["sleep_h"]) for w in well_docs if w.get("sleep_h") is not None]
    fatigue_vals = [float(w["fatigue"]) for w in well_docs if w.get("fatigue") is not None]

    report = {
        "uid": target_uid,
        "athlete_name": athlete_name,
        "sport": ((user or {}).get("athlete_profile") or {}).get("sport"),
        "period": {"from": from_iso, "to": to_iso, "days": days},
        "activities": {
            "count": act_count,
            "total_duration_s": round(total_duration_s),
            "total_distance_km": round(total_distance_m / 1000.0, 2),
            "total_tss": round(total_tss, 1),
        },
        "by_sport": by_sport,
        "pmc": {"start": pmc_start, "end": pmc_end},
        "compliance": {
            "planned_total": planned_total,
            "planned_done": planned_done,
            "pct": round((planned_done / planned_total) * 100, 1) if planned_total > 0 else 100.0,
        },
        "records_power_w": {
            "5s": round(best_5s) if best_5s else None,
            "1m": round(best_60s) if best_60s else None,
            "5m": round(best_300s) if best_300s else None,
            "20m": round(best_1200s) if best_1200s else None,
        },
        "wellness": {
            "entries_count": len(well_docs),
            "avg_sleep_h": round(sum(sleep_vals) / len(sleep_vals), 1) if sleep_vals else None,
            "avg_fatigue": round(sum(fatigue_vals) / len(fatigue_vals), 1) if fatigue_vals else None,
        },
    }
    return report, pmc_docs, by_sport


@router.get("/analytics/report")
async def get_athlete_report(
    org_id: str,
    uid: str | None = Query(default=None),
    days: int = Query(default=30, ge=7, le=365),
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach", "athlete")),
):
    """P5 : Bilan athlète exportable sur N jours (JSON)."""
    report, _, _ = await _compute_athlete_report(org_id, uid, days, ctx)
    return report


@router.get("/analytics/report/pdf")
async def get_athlete_report_pdf(
    org_id: str,
    uid: str | None = Query(default=None),
    days: int = Query(default=30, ge=7, le=365),
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach", "athlete")),
):
    """Export PDF prêt à imprimer / partager du bilan athlète sur N jours."""
    report, pmc_docs, by_sport = await _compute_athlete_report(org_id, uid, days, ctx)
    org = await db_mod.orgs_col.find_one({"_id": org_id}, {"name": 1})
    org_name = (org or {}).get("name") or "Videolog"

    pdf_bytes = render_athlete_report_pdf(
        report,
        org_name=org_name,
        pmc_series=pmc_docs,
        by_sport=by_sport,
    )
    safe_slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", report["athlete_name"]).strip("_") or "athlete"
    filename = f"bilan_{safe_slug}_{report['period']['to']}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
