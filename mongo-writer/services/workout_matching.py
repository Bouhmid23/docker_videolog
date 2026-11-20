"""Appariement automatique prévu <-> réalisé et calcul du score de conformité."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import db as db_mod


def compute_compliance(
    planned: dict[str, Any], activity: dict[str, Any]
) -> dict[str, Any]:
    planned_dur = float(planned.get("estimated_duration_s") or 0)
    actual_dur = float(activity.get("duration_s") or 0)
    planned_tss = planned.get("estimated_tss")
    actual_tss = (activity.get("metrics") or {}).get("tss")

    duration_pct = (
        min(200, round((actual_dur / planned_dur) * 100))
        if planned_dur > 0
        else 100
    )
    tss_pct = (
        min(200, round((float(actual_tss) / float(planned_tss)) * 100))
        if planned_tss and float(planned_tss) > 0 and actual_tss is not None
        else None
    )

    ref_pct = tss_pct if tss_pct is not None else duration_pct
    diff = abs(100 - ref_pct)
    score = max(0, min(100, 100 - diff))

    if 80 <= ref_pct <= 120:
        color = "green"
    elif 50 <= ref_pct <= 150:
        color = "orange"
    else:
        color = "red"

    return {
        "duration_pct": duration_pct,
        "tss_pct": tss_pct,
        "score": score,
        "color": color,
    }


async def match_activity_to_planned(activity_doc: dict[str, Any]) -> dict[str, Any] | None:
    """Associe automatiquement une activité nouvellement importée à une séance planifiée."""
    org_id = activity_doc.get("org_id")
    uid = activity_doc.get("uid")
    start_time = activity_doc.get("start_time")
    if not org_id or not uid or not isinstance(start_time, datetime):
        return None

    date_iso = start_time.date().isoformat()
    candidates = await db_mod.planned_workouts_col.find(
        {
            "org_id": org_id,
            "uid": uid,
            "date": date_iso,
            "sport": activity_doc.get("sport"),
            "activity_id": None,
        }
    ).to_list(20)
    if not candidates:
        return None

    act_dur = float(activity_doc.get("duration_s") or 0)
    best = min(
        candidates,
        key=lambda p: abs(float(p.get("estimated_duration_s") or 0) - act_dur),
    )
    compliance = compute_compliance(best, activity_doc)
    status = "done" if compliance["color"] != "red" else "partial"
    now = datetime.now(UTC)

    await db_mod.planned_workouts_col.update_one(
        {"_id": best["_id"]},
        {
            "$set": {
                "activity_id": activity_doc["_id"],
                "compliance": compliance,
                "status": status,
                "updated_at": now,
            }
        },
    )
    await db_mod.activities_col.update_one(
        {"_id": activity_doc["_id"]},
        {"$set": {"planned_workout_id": best["_id"], "updated_at": now}},
    )
    best["activity_id"] = activity_doc["_id"]
    best["compliance"] = compliance
    best["status"] = status
    return best
