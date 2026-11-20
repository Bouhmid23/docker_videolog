"""Moteur PMC (CTL, ATL, TSB, ramp rate) avec projection sur les séances planifiées."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from pymongo import UpdateOne

import db as db_mod


def compute_pmc_series(
    daily_tss_by_date: dict[str, float],
    start_date: date,
    end_date: date,
    today: date | None = None,
) -> list[dict[str, Any]]:
    ref_today = today or datetime.now(UTC).date()
    ctl = 0.0
    atl = 0.0
    ctl_history: list[float] = []
    rows: list[dict[str, Any]] = []

    cur = start_date
    while cur <= end_date:
        d_iso = cur.isoformat()
        tss = float(daily_tss_by_date.get(d_iso, 0.0))
        tsb = ctl - atl
        ctl = ctl + (tss - ctl) / 42.0
        atl = atl + (tss - atl) / 7.0
        ctl_history.append(ctl)
        ramp_rate = ctl - ctl_history[-8] if len(ctl_history) >= 8 else 0.0

        rows.append(
            {
                "date": d_iso,
                "tss": round(tss, 1),
                "ctl": round(ctl, 1),
                "atl": round(atl, 1),
                "tsb": round(tsb, 1),
                "ramp_rate": round(ramp_rate, 1),
                "is_projected": cur > ref_today,
            }
        )
        cur += timedelta(days=1)
    return rows


async def recompute_pmc(
    org_id: str,
    uid: str,
    *,
    project_days: int = 28,
) -> list[dict[str, Any]]:
    today = datetime.now(UTC).date()
    end_date = today + timedelta(days=project_days)

    activities = await db_mod.activities_col.find(
        {"org_id": org_id, "uid": uid},
        {"start_time": 1, "metrics.tss": 1},
    ).to_list(5000)

    planned = await db_mod.planned_workouts_col.find(
        {
            "org_id": org_id,
            "uid": uid,
            "date": {"$gt": today.isoformat(), "$lte": end_date.isoformat()},
        },
        {"date": 1, "estimated_tss": 1},
    ).to_list(1000)

    daily_tss: dict[str, float] = {}
    earliest: date | None = None

    for a in activities:
        st = a.get("start_time")
        if isinstance(st, datetime):
            d = st.date()
        elif isinstance(st, str):
            d = date.fromisoformat(st[:10])
        else:
            continue
        if earliest is None or d < earliest:
            earliest = d
        tss = float((a.get("metrics") or {}).get("tss") or 0.0)
        d_iso = d.isoformat()
        daily_tss[d_iso] = daily_tss.get(d_iso, 0.0) + tss

    for p in planned:
        d_iso = p["date"]
        daily_tss[d_iso] = daily_tss.get(d_iso, 0.0) + float(
            p.get("estimated_tss") or 0.0
        )

    start_date = min(earliest, today - timedelta(days=30)) if earliest else (today - timedelta(days=60))
    series = compute_pmc_series(daily_tss, start_date, end_date, today=today)

    if series:
        now = datetime.now(UTC)
        ops = [
            UpdateOne(
                {"org_id": org_id, "uid": uid, "date": row["date"]},
                {"$set": {**row, "org_id": org_id, "uid": uid, "updated_at": now}},
                upsert=True,
            )
            for row in series
        ]
        await db_mod.daily_metrics_col.bulk_write(ops, ordered=False)

    return series
