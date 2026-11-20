"""Conversion d'une session vidéo live (room_state + telemetry + recordings) en activités."""
from __future__ import annotations

from datetime import UTC, datetime
import uuid
from typing import Any

import db as db_mod
from services.ingestion import _haversine_m, _resample_1hz, compute_dedup_hash, map_sport
from services.metrics import compute_activity_metrics
from services.pmc import recompute_pmc
from services.workout_matching import match_activity_to_planned
from services.zones import resolve_thresholds_for_date


def _parse_iso_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str) and value.strip():
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        except ValueError:
            return None
    return None


async def convert_room_to_activities(room_name: str) -> list[dict[str, Any]]:
    room = await db_mod.rooms.find_one({"room_name": room_name}, {"_id": 0})
    if not room:
        return []

    org_id = room.get("org_id")
    if not org_id:
        return []

    docs = (
        await db_mod.col.find({"room_name": room_name}, {"_id": 0})
        .sort([("server_ts", 1), ("pts_ms", 1)])
        .to_list(250000)
    )
    if not docs:
        return []

    by_participant: dict[str, list[dict[str, Any]]] = {}
    for d in docs:
        t = d.get("telemetry") or {}
        if t.get("role") in {"observer", "coach"}:
            continue
        raw_pid = str(d.get("participant") or "").strip()
        if not raw_pid:
            continue
        uid_clean = raw_pid.split(":", 1)[0]
        if (
            uid_clean.casefold() in {"telemetry-agent", "telemetry_agent", "egress", "livekit-egress", "unknown"}
            or uid_clean.upper().startswith("EG_")
        ):
            continue
        by_participant.setdefault(uid_clean, []).append(d)

    recordings = (
        await db_mod.recs.find(
            {"room_name": room_name, "status": {"$in": ["completed", "EGRESS_COMPLETE"]}},
            {"_id": 0},
        )
        .sort("started_at", -1)
        .to_list(10)
    )

    planned_sport = ((room.get("planned_workout_snapshot") or {}).get("sport")) or room.get("sport")

    created_activities: list[dict[str, Any]] = []
    for uid, p_docs in by_participant.items():
        source_ref = f"live:{room_name}:{uid}"
        existing = await db_mod.activities_col.find_one(
            {"org_id": org_id, "source_ref": source_ref}, {"_id": 1}
        )
        if existing:
            continue

        samples: list[dict[str, Any]] = []
        gps_dist = 0.0
        speed_dist = 0.0
        prev_pos: tuple[float, float] | None = None
        prev_speed_ts: float | None = None
        prev_speed_val: float = 0.0
        for d in p_docs:
            t = d.get("telemetry") or {}
            gps = t.get("gps") or {}
            ts_ms = d.get("capture_ts") or d.get("server_ts") or d.get("ts")
            if ts_ms is None:
                continue
            ts_s = float(ts_ms) / 1000.0
            lat = gps.get("lat")
            lng = gps.get("lng")
            valid_gps = (
                isinstance(lat, (int, float))
                and isinstance(lng, (int, float))
                and (lat != 0 or lng != 0)
            )
            if valid_gps:
                if prev_pos is not None:
                    gps_dist += _haversine_m(prev_pos[0], prev_pos[1], lat, lng)
                prev_pos = (float(lat), float(lng))
            else:
                lat, lng = None, None

            spd = t.get("speed")
            if isinstance(spd, (int, float)) and spd >= 0:
                if prev_speed_ts is not None:
                    dt_s = ts_s - prev_speed_ts
                    if 0 < dt_s <= 60.0:
                        avg_spd = (prev_speed_val + float(spd)) / 2.0
                        speed_dist += (avg_spd / 3.6) * dt_s
                prev_speed_ts = ts_s
                prev_speed_val = float(spd)

            samples.append(
                {
                    "ts": ts_s,
                    "hr": t.get("heart_rate"),
                    "power": t.get("power_w"),
                    "cadence": t.get("cadence"),
                    "speed": t.get("speed"),
                    "lat": lat,
                    "lng": lng,
                    "alt": gps.get("altitude"),
                }
            )

        if not samples:
            continue

        total_dist = gps_dist if gps_dist > 0 else speed_dist
        start_dt = datetime.fromtimestamp(samples[0]["ts"], UTC)
        streams = _resample_1hz(samples, samples[0]["ts"])
        duration_s = max(0.0, samples[-1]["ts"] - samples[0]["ts"])

        user_doc = await db_mod.users_col.find_one(
            {"_id": uid}, {"athlete_profile.sport": 1}
        )
        raw_sport = planned_sport or ((user_doc or {}).get("athlete_profile") or {}).get("sport")
        sport = map_sport(raw_sport)

        thresholds = await resolve_thresholds_for_date(uid, sport, start_dt)
        metrics = compute_activity_metrics(
            streams,
            duration_s=duration_s,
            distance_m=total_dist,
            sport=sport,
            thresholds=thresholds,
        )

        act_id = uuid.uuid4().hex
        now = datetime.now(UTC)
        act_doc = {
            "_id": act_id,
            "org_id": org_id,
            "uid": uid,
            "sport": sport,
            "title": room.get("title") or f"Séance live — {room_name}",
            "start_time": start_dt,
            "duration_s": round(duration_s, 1),
            "moving_time_s": round(duration_s, 1),
            "distance_m": round(total_dist, 1),
            "elevation_gain_m": metrics.get("elevation_gain_m"),
            "source": "live",
            "source_ref": source_ref,
            "room_name": room_name,
            "dedup_hash": compute_dedup_hash(uid, sport, start_dt, duration_s),
            "thresholds_snapshot": thresholds,
            "metrics": metrics,
            "laps": [],
            "rpe": None,
            "feeling": None,
            "notes": None,
            "created_at": now,
            "updated_at": now,
        }
        await db_mod.activities_col.insert_one(act_doc)
        await db_mod.activity_streams_col.insert_one(
            {"activity_id": act_id, "org_id": org_id, "uid": uid, **streams}
        )
        await match_activity_to_planned(act_doc)
        await recompute_pmc(org_id, uid)

        for rec in recordings:
            files = rec.get("files") or []
            if not files:
                continue
            vid_start = _parse_iso_dt(rec.get("video_started_at")) or _parse_iso_dt(
                rec.get("started_at")
            )
            offset_ms = (
                round((vid_start.timestamp() - start_dt.timestamp()) * 1000.0)
                if vid_start
                else 0
            )
            await db_mod.video_assets_col.insert_one(
                {
                    "_id": uuid.uuid4().hex,
                    "activity_id": act_id,
                    "org_id": org_id,
                    "kind": "egress",
                    "egress_id": rec.get("egress_id"),
                    "s3_key": files[0].get("filename", "").lstrip("/"),
                    "duration_s": files[0].get("duration_s") or files[0].get("duration") or rec.get("duration_s"),
                    "started_at": vid_start,
                    "offset_ms": offset_ms,
                    "created_at": now,
                }
            )

        created_activities.append(act_doc)

    return created_activities
