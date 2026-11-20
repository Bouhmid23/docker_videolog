"""Routes /v1/orgs/{org_id}/sessions : création, liste et exploitation des sessions par organisation."""
from datetime import UTC, datetime, timedelta
import logging
import os
import re
import secrets
from typing import Any
from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, Query

from auth_firebase import OrgContext, require_org_role
from core_config import LK_KEY, LK_SECRET, LK_URL
import db as db_mod
from routers.live_rooms import is_room_joinable
from storage import get_storage
from db import (
    alerts,
    col,
    groups_col,
    markers_col,
    memberships_col,
    orgs_col,
    recs,
    rooms,
    sess,
    users_col,
)
from models import MarkerIn, SessionIn

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/orgs/{org_id}/sessions", tags=["sessions"])


def _serialize_dt(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _sign_mp4_url(filename: str) -> str:
    cleaned = filename.lstrip("/")
    if not cleaned:
        return ""
    bucket = os.environ.get("RECORDINGS_BUCKET", "recordings")
    try:
        return get_storage().presign_get(cleaned, expires_in=3600, bucket=bucket)
    except Exception as e:
        log.warning("Could not presign S3/MinIO URL for %s: %s", cleaned, e)
        public_url = os.environ.get("MINIO_PUBLIC_URL", "http://localhost:9000").rstrip("/")
        return f"{public_url}/{bucket}/{cleaned}"


async def _get_authorized_session(org_id: str, room_name: str, ctx: OrgContext) -> dict:
    session = await rooms.find_one({"room_name": room_name})
    if not session or session.get("org_id") != org_id:
        raise HTTPException(status_code=404, detail="Session introuvable.")
    if ctx.role == "athlete":
        participant_uids = session.get("participant_uids") or []
        if ctx.account.uid not in participant_uids:
            raise HTTPException(status_code=403, detail="Accès refusé à cette session.")
    return session



def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (slug[:24] or "org").strip("-")


@router.get("")
async def list_org_sessions(
    org_id: str,
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach", "athlete")),
):
    query: dict = {"org_id": org_id}
    if ctx.role == "athlete":
        query["participant_uids"] = ctx.account.uid
    elif ctx.role == "coach" and ctx.membership:
        coach_groups = ctx.membership.group_ids or []
        query["$or"] = [
            {"owner_uid": ctx.account.uid},
            {"participant_uids": ctx.account.uid},
            *(
                [{"group_id": {"$in": coach_groups}}]
                if coach_groups
                else []
            ),
        ]

    docs = await rooms.find(query, {"_id": 0}).sort("updated_at", -1).to_list(length=100)
    for d in docs:
        joinable, joinable_reason = is_room_joinable(d)
        d["joinable"] = joinable
        d["joinable_reason"] = joinable_reason
        for k in ("created_at", "updated_at", "scheduled_at", "scheduled_end_at"):
            if isinstance(d.get(k), datetime):
                d[k] = d[k].isoformat()
    return {"sessions": docs, "count": len(docs)}


@router.post("", status_code=201)
async def create_org_session(
    org_id: str,
    body: SessionIn,
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach")),
):
    org = await orgs_col.find_one({"_id": org_id})
    if not org:
        raise HTTPException(status_code=404, detail="Organisation introuvable.")

    if body.group_id:
        grp = await groups_col.find_one({"_id": body.group_id, "org_id": org_id})
        if not grp:
            raise HTTPException(status_code=422, detail="Groupe invalide.")
        if ctx.role == "coach" and ctx.membership:
            if body.group_id not in ctx.membership.group_ids:
                raise HTTPException(status_code=403, detail="Accès refusé à ce groupe.")

    participant_uids = set(body.participant_uids)
    participant_uids.add(ctx.account.uid)

    planned_snapshot: dict | None = None
    planned_doc: dict | None = None
    if body.planned_workout_id:
        planned = await db_mod.planned_workouts_col.find_one(
            {"_id": body.planned_workout_id, "org_id": org_id}
        )
        if not planned:
            raise HTTPException(status_code=422, detail="Séance planifiée introuvable.")
        if planned.get("status") == "done":
            raise HTTPException(
                status_code=409,
                detail="Cette séance planifiée est déjà marquée comme réalisée.",
            )
        existing_room = await rooms.find_one(
            {
                "org_id": org_id,
                "planned_workout_id": body.planned_workout_id,
                "finished": {"$ne": True},
            },
            {"_id": 0},
        )
        if existing_room and is_room_joinable(existing_room)[0]:
            out = dict(existing_room)
            out["joinable"] = True
            out["joinable_reason"] = None
            for k in ("created_at", "updated_at", "scheduled_at", "scheduled_end_at"):
                if isinstance(out.get(k), datetime):
                    out[k] = out[k].isoformat()
            return {"session": out}
        planned_doc = planned
        if planned.get("uid"):
            participant_uids.add(planned["uid"])
        planned_snapshot = {
            "id": planned["_id"],
            "title": planned.get("title"),
            "sport": planned.get("sport"),
            "date": planned.get("date"),
            "start_time": planned.get("start_time"),
            "estimated_duration_s": planned.get("estimated_duration_s"),
            "estimated_tss": planned.get("estimated_tss"),
            "steps": planned.get("steps") or [],
        }

    if body.group_id and not body.participant_uids:
        group_members = await memberships_col.find(
            {"org_id": org_id, "group_ids": body.group_id, "status": "active"}
        ).to_list(length=200)
        for m in group_members:
            participant_uids.add(m["uid"])

    if participant_uids:
        valid_members = await memberships_col.find(
            {"org_id": org_id, "uid": {"$in": list(participant_uids)}, "status": "active"}
        ).to_list(length=200)
        valid_uids = {m["uid"] for m in valid_members}
        if not ctx.account.platform_admin and len(valid_uids) != len(participant_uids):
            raise HTTPException(
                status_code=422,
                detail="Certains participants ne sont pas membres actifs de l'organisation.",
            )

    now = datetime.now(UTC)
    duration_s = (
        body.duration_s
        or (int(planned_doc["estimated_duration_s"]) if planned_doc and planned_doc.get("estimated_duration_s") else None)
        or 3600
    )
    scheduled_at = body.scheduled_at
    if scheduled_at is None and planned_doc and planned_doc.get("date") and planned_doc.get("start_time"):
        try:
            scheduled_at = datetime.fromisoformat(
                f"{planned_doc['date']}T{planned_doc['start_time']}:00+00:00"
            )
        except ValueError:
            scheduled_at = None
    scheduled_end_at = (
        (scheduled_at if scheduled_at.tzinfo else scheduled_at.replace(tzinfo=UTC))
        + timedelta(seconds=duration_s)
        if scheduled_at is not None
        else None
    )

    slug = _slugify(org.get("name", "org"))
    room_name = f"{slug}-{now.strftime('%Y%m%d')}-{secrets.token_hex(3)}"

    room_doc = {
        "room_name": room_name,
        "org_id": org_id,
        "owner_uid": ctx.account.uid,
        "group_id": body.group_id,
        "title": body.title,
        "scheduled_at": scheduled_at,
        "scheduled_end_at": scheduled_end_at,
        "duration_s": duration_s,
        "planned_workout_id": body.planned_workout_id,
        "planned_workout_snapshot": planned_snapshot,
        "participant_uids": sorted(participant_uids),
        "recording_operator_uids": [ctx.account.uid],
        "participants": [],
        "count": 0,
        "egress_ids": [],
        "recording": False,
        "finished": False,
        "created_at": now,
        "updated_at": now,
    }
    await rooms.insert_one(room_doc)
    await sess.update_one(
        {"room_name": room_name},
        {
            "$setOnInsert": {
                "room_name": room_name,
                "org_id": org_id,
                "started_at": now.isoformat(),
                "status": "waiting",
            }
        },
        upsert=True,
    )
    out = dict(room_doc)
    out.pop("_id", None)
    for k in ("created_at", "updated_at", "scheduled_at", "scheduled_end_at"):
        if isinstance(out.get(k), datetime):
            out[k] = out[k].isoformat()
    return {"session": out}


@router.get("/{room_name}")
async def get_session_detail(
    org_id: str,
    room_name: str,
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach", "athlete")),
):
    session = await _get_authorized_session(org_id, room_name, ctx)

    # Enrich participants list with display_name, email and role
    participant_uids = list(session.get("participant_uids") or [])
    users_by_id = {}
    if participant_uids:
        user_docs = await users_col.find(
            {"_id": {"$in": participant_uids}}
        ).to_list(length=len(participant_uids) + 10)
        users_by_id = {u["_id"]: u for u in user_docs}

    member_docs = await memberships_col.find(
        {"org_id": org_id, "uid": {"$in": participant_uids}}
    ).to_list(length=len(participant_uids) + 10) if participant_uids else []
    roles_by_uid = {m["uid"]: m.get("role", "athlete") for m in member_docs}

    participants = []
    for uid in participant_uids:
        u = users_by_id.get(uid, {})
        fn = u.get("first_name", "")
        ln = u.get("last_name", "")
        name = f"{fn} {ln}".strip()
        disp = name or u.get("display_name") or u.get("email") or uid
        role = roles_by_uid.get(uid, "athlete")
        participants.append({
            "uid": uid,
            "identity": uid,
            "display_name": disp,
            "email": u.get("email"),
            "role": role,
        })

    # Recordings and signed MP4 URLs (1h expiration)
    rec_docs = await recs.find({"room_name": room_name}).to_list(length=100)
    if not rec_docs and session.get("recordings"):
        rec_docs = session.get("recordings") or []

    recordings = []
    for r in rec_docs:
        rec_status = r.get("status", "EGRESS_COMPLETE")
        if rec_status in {"failed", "EGRESS_FAILED", "EGRESS_ABORTED"}:
            continue
        files = r.get("files") or []
        if files and all(
            (f.get("size") or f.get("size_bytes") or 0) == 0
            for f in files
        ) and rec_status in {"completed", "EGRESS_COMPLETE", "stopped"}:
            continue
        mp4_filename = ""
        duration_s = r.get("duration_s")
        if duration_s is None:
            duration_s = r.get("duration")

        for f in files:
            fname = f.get("filename", "")
            if fname.lower().endswith(".mp4"):
                mp4_filename = fname
                if duration_s is None:
                    duration_s = f.get("duration")
                break
            elif not mp4_filename and fname:
                mp4_filename = fname
                if duration_s is None:
                    duration_s = f.get("duration")

        if not mp4_filename:
            mp4_filename = r.get("filename") or r.get("filepath") or ""

        if mp4_filename:
            signed_url = _sign_mp4_url(mp4_filename)
        else:
            signed_url = r.get("mp4_url") or r.get("url") or ""

        video_started_at = _serialize_dt(
            r.get("video_started_at") or r.get("started_at")
        )

        recordings.append({
            "egress_id": r.get("egress_id") or str(r.get("_id", "")),
            "status": r.get("status", "EGRESS_COMPLETE"),
            "mp4_url": signed_url,
            "duration_s": float(duration_s) if duration_s is not None else 0.0,
            "video_started_at": video_started_at,
        })

    recording_status = (session.get("mobile_recording") or {}).get("status", "not_started")
    if session.get("recording"):
        recording_status = "recording"

    joinable, joinable_reason = is_room_joinable(session)
    return {
        "room_name": session["room_name"],
        "title": session.get("title", ""),
        "org_id": session.get("org_id", org_id),
        "group_id": session.get("group_id"),
        "owner_uid": session.get("owner_uid"),
        "created_at": _serialize_dt(session.get("created_at")),
        "updated_at": _serialize_dt(session.get("updated_at")),
        "scheduled_at": _serialize_dt(session.get("scheduled_at")),
        "scheduled_end_at": _serialize_dt(session.get("scheduled_end_at")),
        "duration_s": session.get("duration_s"),
        "finished": session.get("finished", False),
        "joinable": joinable,
        "joinable_reason": joinable_reason,
        "recording": session.get("recording", False),
        "recording_status": recording_status,
        "participant_uids": participant_uids,
        "participants": participants,
        "recordings": recordings,
    }


@router.post("/{room_name}/finish")
async def finish_org_session(
    org_id: str,
    room_name: str,
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach")),
):
    """Termine explicitement une session live et bloque tout nouvel accès."""
    await _get_authorized_session(org_id, room_name, ctx)
    now = datetime.now(UTC)
    await rooms.update_one(
        {"room_name": room_name, "org_id": org_id},
        {
            "$set": {
                "finished": True,
                "finished_at": now,
                "finished_by": ctx.account.uid,
                "count": 0,
                "participants": [],
                "recording": False,
                "updated_at": now,
            }
        },
    )
    await sess.update_one(
        {"room_name": room_name},
        {"$set": {"status": "finished", "ended_at": now.isoformat()}},
    )
    if LK_KEY and LK_SECRET and LK_URL:
        try:
            from livekit import api as lkapi

            async with lkapi.LiveKitAPI(LK_URL, LK_KEY, LK_SECRET) as lk:
                await lk.room.delete_room(lkapi.DeleteRoomRequest(room=room_name))
        except Exception:
            log.debug("LiveKit delete_room ignoré pour %s (déjà fermée ou hors ligne)", room_name)

    converted_activities: list[dict[str, Any]] = []
    try:
        from services.live_to_activity import convert_room_to_activities

        converted_activities = await convert_room_to_activities(room_name)
    except Exception:
        log.exception("Conversion auto Live -> Activité échouée pour %s", room_name)

    return {
        "status": "finished",
        "room_name": room_name,
        "converted_count": len(converted_activities),
        "activity_ids": [str(a.get("_id")) for a in converted_activities if a.get("_id")],
    }


def _participant_identity_filter(raw_uid: str) -> dict[str, Any] | str:
    base_uid = str(raw_uid or "").split(":", 1)[0].strip()
    if not base_uid:
        return raw_uid
    return {"$in": [base_uid, f"{base_uid}:web"]}


@router.get("/{room_name}/telemetry")
async def get_session_telemetry(
    org_id: str,
    room_name: str,
    participant: str | None = None,
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach", "athlete")),
):
    await _get_authorized_session(org_id, room_name, ctx)

    query: dict[str, Any] = {"room_name": room_name}
    if ctx.role == "athlete":
        query["participant"] = _participant_identity_filter(ctx.account.uid)
    elif participant:
        query["participant"] = _participant_identity_filter(participant)

    docs = await col.find(query, {"_id": 0}).sort("pts_ms", 1).to_list(length=100_000)

    samples = []
    for d in docs:
        t = d.get("telemetry") or {}
        gps = t.get("gps") if isinstance(t.get("gps"), dict) else {}
        speed = d.get("speed") if d.get("speed") is not None else t.get("speed", 0.0)
        heart_rate = d.get("heart_rate") if d.get("heart_rate") is not None else t.get("heart_rate", 0)
        cadence = d.get("cadence") if d.get("cadence") is not None else t.get("cadence", 0)
        power_w = d.get("power_w") if d.get("power_w") is not None else t.get("power_w")
        lat = d.get("lat") if d.get("lat") is not None else gps.get("lat")
        lng = d.get("lng") if d.get("lng") is not None else gps.get("lng")
        altitude = d.get("altitude") if d.get("altitude") is not None else gps.get("altitude")
        bearing = (
            d.get("bearing")
            if d.get("bearing") is not None
            else (t.get("bearing") if t.get("bearing") is not None else gps.get("bearing"))
        )
        server_ts = d.get("server_ts") if d.get("server_ts") is not None else d.get("ts", 0.0)

        samples.append({
            "pts_ms": float(d.get("pts_ms", 0.0)),
            "server_ts": float(server_ts) if server_ts is not None else 0.0,
            "participant": str(d.get("participant", "")).split(":", 1)[0],
            "speed": speed,
            "heart_rate": heart_rate,
            "cadence": cadence,
            "power_w": power_w,
            "lat": lat,
            "lng": lng,
            "altitude": altitude,
            "bearing": bearing,
        })
    return samples


@router.get("/{room_name}/alerts")
async def get_session_alerts(
    org_id: str,
    room_name: str,
    participant: str | None = None,
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach", "athlete")),
):
    await _get_authorized_session(org_id, room_name, ctx)

    query: dict[str, Any] = {"room_name": room_name}
    if ctx.role == "athlete":
        query["participant"] = _participant_identity_filter(ctx.account.uid)
    elif participant:
        query["participant"] = _participant_identity_filter(participant)

    docs = await alerts.find(query).sort([("pts_ms", 1), ("ts", 1)]).to_list(length=10_000)

    out = []
    for d in docs:
        aid = str(d.get("_id", "")) if "_id" in d else str(d.get("id", ""))
        out.append({
            "id": aid,
            "type": d.get("type", ""),
            "value": d.get("value"),
            "threshold": d.get("threshold"),
            "participant": d.get("participant", ""),
            "pts_ms": float(d.get("pts_ms", 0.0)) if d.get("pts_ms") is not None else 0.0,
            "message": d.get("message", ""),
            "ts": float(d.get("ts", 0.0)) if d.get("ts") is not None else 0.0,
        })
    return out


@router.get("/{room_name}/markers")
async def list_session_markers(
    org_id: str,
    room_name: str,
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach", "athlete")),
):
    await _get_authorized_session(org_id, room_name, ctx)

    docs = await markers_col.find(
        {"org_id": org_id, "room_name": room_name}
    ).sort("pts_ms", 1).to_list(length=1000)

    out = []
    for d in docs:
        mid = str(d.get("_id", "")) if "_id" in d else str(d.get("id", ""))
        out.append({
            "id": mid,
            "room_name": d.get("room_name", room_name),
            "org_id": d.get("org_id", org_id),
            "pts_ms": float(d.get("pts_ms", 0.0)),
            "label": d.get("label", ""),
            "note": d.get("note"),
            "category": d.get("category", "general"),
            "created_by": d.get("created_by", ""),
            "author_name": d.get("author_name", ""),
            "created_at": _serialize_dt(d.get("created_at")),
        })
    return out


@router.post("/{room_name}/markers", status_code=201)
async def create_session_marker(
    org_id: str,
    room_name: str,
    body: MarkerIn,
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach", "athlete")),
):
    if ctx.role == "athlete":
        raise HTTPException(
            status_code=403,
            detail="Accès refusé : la création de marqueurs est réservée aux coachs et administrateurs.",
        )
    await _get_authorized_session(org_id, room_name, ctx)

    now = datetime.now(UTC)
    author_name = "Coach"
    if ctx.account.user_doc:
        fn = ctx.account.user_doc.get("first_name", "")
        ln = ctx.account.user_doc.get("last_name", "")
        name = f"{fn} {ln}".strip()
        if name:
            author_name = name
        elif ctx.account.user_doc.get("display_name"):
            author_name = ctx.account.user_doc["display_name"]
        elif ctx.account.email:
            author_name = ctx.account.email
    elif ctx.account.email:
        author_name = ctx.account.email

    doc = {
        "org_id": org_id,
        "room_name": room_name,
        "pts_ms": float(body.pts_ms),
        "label": body.label,
        "note": body.note,
        "category": body.category,
        "created_by": ctx.account.uid,
        "author_name": author_name,
        "created_at": now,
    }
    res = await markers_col.insert_one(doc)

    out = dict(doc)
    out["id"] = str(res.inserted_id)
    out.pop("_id", None)
    out["created_at"] = now.isoformat()
    return out


@router.delete("/{room_name}/markers/{marker_id}")
async def delete_session_marker(
    org_id: str,
    room_name: str,
    marker_id: str,
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach", "athlete")),
):
    if ctx.role == "athlete":
        raise HTTPException(
            status_code=403,
            detail="Accès refusé : la suppression de marqueurs est réservée aux coachs et administrateurs.",
        )
    await _get_authorized_session(org_id, room_name, ctx)

    candidate_ids: list[Any] = [marker_id]
    try:
        candidate_ids.append(ObjectId(marker_id))
    except (InvalidId, TypeError):
        pass

    res = await markers_col.delete_one({
        "_id": {"$in": candidate_ids},
        "org_id": org_id,
        "room_name": room_name,
    })
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Marqueur introuvable.")

    return {"ok": True, "deleted": marker_id}

