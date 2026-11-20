"""Routes /mobile/rooms et /rooms/{room_name}/lobby (gestion authentifiée des rooms LiveKit)."""
from datetime import UTC, datetime
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from auth_firebase import Account, can_control_recording, can_join_room, current_account
import db as db_mod

log = logging.getLogger(__name__)
router = APIRouter(tags=["live-rooms"])


def _parse_utc_dt(val: Any) -> datetime | None:
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=UTC)
    if isinstance(val, str) and val:
        try:
            dt = datetime.fromisoformat(val)
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        except ValueError:
            return None
    return None


def is_room_joinable(room_doc: dict[str, Any], now: datetime | None = None) -> tuple[bool, str | None]:
    """Vérifie si une room est accessible : pendant sa durée prévue OU tant que des participants sont connectés."""
    if room_doc.get("finished"):
        return False, "Session terminée."
    if int(room_doc.get("count") or 0) > 0:
        return True, None
    end_dt = _parse_utc_dt(room_doc.get("scheduled_end_at"))
    if end_dt is not None:
        ref = now or datetime.now(UTC)
        if ref > end_dt:
            return (
                False,
                "L'horaire de cette session est dépassé et aucun participant n'est connecté.",
            )
    return True, None


@router.get("/rooms/{room_name}/lobby")
async def get_room_lobby(
    room_name: str,
    account: Account = Depends(current_account),
):
    """Informations enrichies pour le lobby de connexion (/live/)."""
    room_doc = await db_mod.rooms.find_one({"room_name": room_name}, {"_id": 0})
    if not room_doc:
        raise HTTPException(status_code=404, detail="Session introuvable.")
    if not can_join_room(account, room_doc):
        raise HTTPException(
            status_code=403, detail="Vous n'êtes pas autorisé à rejoindre cette session."
        )

    org = (
        await db_mod.orgs_col.find_one({"_id": room_doc["org_id"]}, {"name": 1})
        if room_doc.get("org_id")
        else None
    )
    participant_uids = list(room_doc.get("participant_uids") or [])
    connected_raw = list(room_doc.get("participants") or [])
    connected_uids = {
        uid
        for uid in (
            (
                str(p.get("identity") or "")
                if isinstance(p, dict)
                else str(p or "")
            ).split(":", 1)[0]
            for p in connected_raw
            if isinstance(p, (dict, str)) and p
        )
        if uid
        and uid.casefold() not in {"telemetry-agent", "telemetry_agent", "egress", "livekit-egress"}
        and not uid.upper().startswith("EG_")
    }

    users_map: dict[str, dict] = {}
    if participant_uids:
        async for u in db_mod.users_col.find(
            {"_id": {"$in": participant_uids}},
            {
                "first_name": 1,
                "last_name": 1,
                "email": 1,
                "athlete_profile": 1,
                "health_consent": 1,
            },
        ):
            users_map[u["_id"]] = u

    invited_participants = []
    for uid in participant_uids:
        u = users_map.get(uid, {})
        name = f"{u.get('first_name') or ''} {u.get('last_name') or ''}".strip() or u.get("email") or uid
        invited_participants.append(
            {
                "uid": uid,
                "name": name,
                "is_owner": uid == room_doc.get("owner_uid"),
                "connected": uid in connected_uids,
            }
        )

    # Résolution batch des seuils (FTP / LTHR / FC max / FC repos) pour tous les participants
    participant_thresholds: dict[str, dict[str, Any]] = {}
    if participant_uids and room_doc.get("org_id"):
        thr_cursor = db_mod.threshold_history_col.find(
            {"org_id": room_doc["org_id"], "uid": {"$in": participant_uids}},
            {"_id": 0, "uid": 1, "ftp_w": 1, "lthr_bpm": 1, "hr_max": 1, "hr_rest": 1},
        ).sort([("effective_from", -1), ("created_at", -1)])
        async for thr in thr_cursor:
            t_uid = thr.get("uid")
            if t_uid and t_uid not in participant_thresholds:
                participant_thresholds[t_uid] = {
                    "ftp_w": thr.get("ftp_w"),
                    "lthr_bpm": thr.get("lthr_bpm"),
                    "hr_max": thr.get("hr_max"),
                    "hr_rest": thr.get("hr_rest"),
                }

    for uid in participant_uids:
        u = users_map.get(uid, {})
        consent = u.get("health_consent") or {}
        profile = u.get("athlete_profile") or {}
        if consent.get("granted") is True and isinstance(profile, dict):
            entry = participant_thresholds.setdefault(uid, {})
            if entry.get("hr_max") is None and profile.get("hr_max") is not None:
                entry["hr_max"] = profile.get("hr_max")
            if entry.get("hr_rest") is None and profile.get("hr_rest") is not None:
                entry["hr_rest"] = profile.get("hr_rest")
        # Ne garder que les entrées ayant au moins un seuil renseigné
        if uid in participant_thresholds and not any(
            v is not None for v in participant_thresholds[uid].values()
        ):
            participant_thresholds.pop(uid, None)

    accessible, reason = is_room_joinable(room_doc)
    sched_at = _parse_utc_dt(room_doc.get("scheduled_at"))
    sched_end = _parse_utc_dt(room_doc.get("scheduled_end_at"))

    planned_workout = room_doc.get("planned_workout_snapshot")
    if isinstance(planned_workout, dict):
        athlete_uid = next(
            (u for u in participant_uids if u != room_doc.get("owner_uid")),
            room_doc.get("owner_uid"),
        )
        if athlete_uid and athlete_uid in participant_thresholds:
            thr = participant_thresholds[athlete_uid]
            planned_workout = {
                **planned_workout,
                "thresholds": {
                    "ftp_w": thr.get("ftp_w"),
                    "lthr_bpm": thr.get("lthr_bpm"),
                    "hr_max": thr.get("hr_max"),
                },
            }

    return {
        "room_name": room_name,
        "title": room_doc.get("title") or room_name,
        "org_name": (org or {}).get("name"),
        "duration_s": room_doc.get("duration_s") or 3600,
        "scheduled_at": sched_at.isoformat() if sched_at else None,
        "scheduled_end_at": sched_end.isoformat() if sched_end else None,
        "active_count": int(room_doc.get("count") or 0),
        "finished": bool(room_doc.get("finished")),
        "invited_participants": invited_participants,
        "participant_thresholds": participant_thresholds,
        "planned_workout": planned_workout,
        "accessible": accessible,
        "reason": reason,
    }


@router.get("/mobile/rooms")
async def list_mobile_rooms(
    limit: int = Query(default=50, ge=1, le=100),
    account: Account = Depends(current_account),
):
    query: dict[str, Any] = {"finished": {"$ne": True}}
    projection = {
        "_id": 0,
        "room_name": 1,
        "org_id": 1,
        "owner_uid": 1,
        "group_id": 1,
        "title": 1,
        "participant_uids": 1,
        "recording_operator_uids": 1,
        "count": 1,
        "recording": 1,
        "finished": 1,
        "scheduled_at": 1,
        "scheduled_end_at": 1,
        "duration_s": 1,
        "created_at": 1,
        "updated_at": 1,
        "mobile_recording.status": 1,
    }
    room_docs = (
        await db_mod.rooms.find(query, projection)
        .sort("updated_at", -1)
        .limit(200)
        .to_list(length=200)
    )

    result = []
    for room in room_docs:
        if not can_join_room(account, room):
            continue
        joinable, _ = is_room_joinable(room)
        if not joinable:
            continue
        for field in ("created_at", "updated_at"):
            if isinstance(room.get(field), datetime):
                room[field] = room[field].isoformat()
        result.append(
            {
                "room_name": room["room_name"],
                "title": room.get("title"),
                "org_id": room.get("org_id"),
                "count": room.get("count", 0),
                "recording": room.get("recording", False),
                "recording_status": (room.get("mobile_recording") or {}).get(
                    "status", "not_started"
                ),
                "created_at": room.get("created_at"),
                "updated_at": room.get("updated_at"),
                "can_control_recording": can_control_recording(account, room),
            }
        )
        if len(result) >= limit:
            break
    return {"rooms": result, "count": len(result)}

