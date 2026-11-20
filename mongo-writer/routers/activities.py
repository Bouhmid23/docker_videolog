"""Routes /v1/orgs/{org_id}/activities : upload FIT/GPX/TCX, liste, détail, flux 1 Hz, commentaires."""
from datetime import UTC, datetime
import logging
import os
from typing import Any
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile

from auth_firebase import OrgContext, require_org_role
import db as db_mod
from mailer import send_comment_notification_email
from models import ActivityCommentIn, ActivityPatchIn, ActivityShareIn
from services.ingestion import compute_dedup_hash, parse_activity_file
from services.live_to_activity import convert_room_to_activities
from services.metrics import compute_activity_metrics
from services.pmc import recompute_pmc
from services.workout_matching import match_activity_to_planned
from services.zones import build_hr_zones, build_power_zones, resolve_thresholds_for_date
from storage import get_storage

log = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/orgs/{org_id}/activities", tags=["activities"])

MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 Mo


def _serialize_activity(doc: dict[str, Any], *, include_laps: bool = False) -> dict[str, Any]:
    start_time = doc.get("start_time")
    out = {
        "id": str(doc["_id"]),
        "org_id": doc.get("org_id"),
        "uid": doc.get("uid"),
        "sport": doc.get("sport"),
        "title": doc.get("title"),
        "start_time": start_time.isoformat()
        if isinstance(start_time, datetime)
        else start_time,
        "duration_s": doc.get("duration_s", 0),
        "moving_time_s": doc.get("moving_time_s", 0),
        "distance_m": doc.get("distance_m", 0),
        "elevation_gain_m": doc.get("elevation_gain_m"),
        "source": doc.get("source"),
        "room_name": doc.get("room_name"),
        "planned_workout_id": doc.get("planned_workout_id"),
        "metrics": doc.get("metrics") or {},
        "rpe": doc.get("rpe"),
        "feeling": doc.get("feeling"),
        "notes": doc.get("notes"),
        "shared_org_ids": doc.get("shared_org_ids") or [],
        "created_at": doc["created_at"].isoformat()
        if isinstance(doc.get("created_at"), datetime)
        else doc.get("created_at"),
    }
    if include_laps:
        out["laps"] = doc.get("laps") or []
        out["thresholds_snapshot"] = doc.get("thresholds_snapshot") or {}
    return out


async def _get_authorized_activity(
    org_id: str, activity_id: str, ctx: OrgContext
) -> dict[str, Any]:
    doc = await db_mod.activities_col.find_one({"_id": activity_id})
    if not doc or (
        doc.get("org_id") != org_id and org_id not in (doc.get("shared_org_ids") or [])
    ):
        raise HTTPException(status_code=404, detail="Activité introuvable.")
    if ctx.role == "athlete" and doc.get("uid") != ctx.account.uid:
        raise HTTPException(status_code=403, detail="Accès refusé à cette activité.")
    return doc


@router.post("/upload", status_code=201)
async def upload_activity(
    org_id: str,
    file: UploadFile = File(...),
    athlete_uid: str | None = Form(default=None),
    title: str | None = Form(default=None),
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach", "athlete")),
):
    target_uid = ctx.account.uid
    if athlete_uid and athlete_uid.strip():
        if ctx.role == "athlete" and athlete_uid.strip() != ctx.account.uid:
            raise HTTPException(
                status_code=403,
                detail="Un athlète ne peut importer que ses propres activités.",
            )
        target_uid = athlete_uid.strip()
        member = await db_mod.memberships_col.find_one(
            {"org_id": org_id, "uid": target_uid, "status": "active"}
        )
        if not member:
            raise HTTPException(
                status_code=404,
                detail="Athlète cible introuvable dans cette organisation.",
            )

    filename = file.filename or "activity.fit"
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=422, detail="Le fichier est vide.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413, detail="Le fichier dépasse la limite de 25 Mo."
        )

    try:
        parsed = parse_activity_file(filename, raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    dedup_hash = compute_dedup_hash(
        target_uid, parsed.sport, parsed.start_time, parsed.duration_s
    )
    duplicate = await db_mod.activities_col.find_one(
        {"org_id": org_id, "dedup_hash": dedup_hash}, {"_id": 1}
    )
    if duplicate:
        raise HTTPException(
            status_code=409,
            detail="Cette activité a déjà été importée (doublon détecté).",
        )

    act_id = uuid.uuid4().hex
    raw_key = f"activities/{org_id}/{target_uid}/{act_id}/{os.path.basename(filename)}"
    try:
        get_storage().put_bytes(raw_key, raw)
    except Exception as exc:
        log.warning("Could not store raw activity file in S3 (%s): %s", raw_key, exc)
        raw_key = None

    thresholds = await resolve_thresholds_for_date(
        target_uid, parsed.sport, parsed.start_time
    )
    metrics = compute_activity_metrics(
        parsed.streams,
        duration_s=parsed.duration_s,
        distance_m=parsed.distance_m,
        sport=parsed.sport,
        thresholds=thresholds,
    )

    ext = os.path.splitext(filename)[1].lstrip(".").lower() or "fit"
    cleaned_title = (title or "").strip() or parsed.title or f"{parsed.sport.capitalize()} ({ext.upper()})"
    now = datetime.now(UTC)

    doc = {
        "_id": act_id,
        "org_id": org_id,
        "uid": target_uid,
        "sport": parsed.sport,
        "title": cleaned_title,
        "start_time": parsed.start_time,
        "duration_s": round(parsed.duration_s, 1),
        "moving_time_s": round(parsed.moving_time_s, 1),
        "distance_m": round(parsed.distance_m, 1),
        "elevation_gain_m": metrics.get("elevation_gain_m"),
        "source": ext,
        "source_ref": raw_key,
        "dedup_hash": dedup_hash,
        "thresholds_snapshot": thresholds,
        "metrics": metrics,
        "laps": parsed.laps,
        "rpe": None,
        "feeling": None,
        "notes": None,
        "created_at": now,
        "updated_at": now,
    }
    await db_mod.activities_col.insert_one(doc)
    await db_mod.activity_streams_col.insert_one(
        {"activity_id": act_id, "org_id": org_id, "uid": target_uid, **parsed.streams}
    )
    matched = await match_activity_to_planned(doc)
    if matched:
        doc["planned_workout_id"] = matched["_id"]
    await recompute_pmc(org_id, target_uid)

    return {"activity": _serialize_activity(doc, include_laps=True)}


@router.get("")
async def list_activities(
    org_id: str,
    uid: str | None = Query(default=None),
    sport: str | None = Query(default=None),
    from_date: str | None = Query(default=None, alias="from"),
    to_date: str | None = Query(default=None, alias="to"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach", "athlete")),
):
    query: dict[str, Any] = {
        "$or": [{"org_id": org_id}, {"shared_org_ids": org_id}]
    }
    if ctx.role == "athlete":
        query["uid"] = ctx.account.uid
    elif uid:
        query["uid"] = uid

    if sport:
        query["sport"] = sport
    if from_date or to_date:
        dt_range: dict[str, datetime] = {}
        if from_date:
            dt_range["$gte"] = datetime.fromisoformat(f"{from_date}T00:00:00+00:00")
        if to_date:
            dt_range["$lte"] = datetime.fromisoformat(f"{to_date}T23:59:59+00:00")
        query["start_time"] = dt_range

    total = await db_mod.activities_col.count_documents(query)
    docs = (
        await db_mod.activities_col.find(query)
        .sort("start_time", -1)
        .skip(offset)
        .limit(limit)
        .to_list(limit)
    )

    # Enrichir avec nom de l'athlète
    uids = list({d["uid"] for d in docs if d.get("uid")})
    users_map: dict[str, str] = {}
    if uids:
        async for u in db_mod.users_col.find(
            {"_id": {"$in": uids}}, {"first_name": 1, "last_name": 1, "email": 1}
        ):
            name = f"{u.get('first_name') or ''} {u.get('last_name') or ''}".strip()
            users_map[u["_id"]] = name or u.get("email") or u["_id"]

    items = []
    for d in docs:
        item = _serialize_activity(d)
        item["athlete_name"] = users_map.get(d["uid"], d["uid"])
        items.append(item)

    return {"items": items, "total": total}


@router.post("/convert-live/{room_name}")
async def convert_live_session(
    org_id: str,
    room_name: str,
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach")),
):
    room = await db_mod.rooms.find_one({"room_name": room_name}, {"org_id": 1})
    if not room or room.get("org_id") != org_id:
        raise HTTPException(status_code=404, detail="Session introuvable.")
    created = await convert_room_to_activities(room_name)
    return {
        "converted_count": len(created),
        "activities": [_serialize_activity(a) for a in created],
    }


@router.get("/{activity_id}")
async def get_activity(
    org_id: str,
    activity_id: str,
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach", "athlete")),
):
    doc = await _get_authorized_activity(org_id, activity_id, ctx)
    user = await db_mod.users_col.find_one(
        {"_id": doc["uid"]}, {"first_name": 1, "last_name": 1, "email": 1}
    )
    athlete_name = (
        f"{(user or {}).get('first_name') or ''} {(user or {}).get('last_name') or ''}".strip()
        or (user or {}).get("email")
        or doc["uid"]
    )

    thresholds = doc.get("thresholds_snapshot") or {}
    zones = {
        "power_zones": build_power_zones(thresholds.get("ftp_w")),
        "hr_zones": build_hr_zones(
            thresholds.get("lthr_bpm"), thresholds.get("hr_max")
        ),
    }

    videos_raw = await db_mod.video_assets_col.find(
        {"activity_id": activity_id}
    ).to_list(20)
    bucket = os.environ.get("RECORDINGS_BUCKET", "recordings")
    storage = get_storage()
    videos = []
    for v in videos_raw:
        s3_key = v.get("s3_key") or ""
        mp4_url = ""
        if s3_key:
            try:
                mp4_url = storage.presign_get(s3_key, expires_in=3600, bucket=bucket)
            except Exception:
                mp4_url = ""
        videos.append(
            {
                "id": str(v["_id"]),
                "kind": v.get("kind"),
                "mp4_url": mp4_url,
                "duration_s": v.get("duration_s"),
                "offset_ms": v.get("offset_ms", 0),
            }
        )

    out = _serialize_activity(doc, include_laps=True)
    out["athlete_name"] = athlete_name
    out["zones"] = zones
    out["video_assets"] = videos

    pw_query = (
        {"_id": doc["planned_workout_id"]}
        if doc.get("planned_workout_id")
        else {"activity_id": activity_id}
    )
    pw_doc = await db_mod.planned_workouts_col.find_one(pw_query)
    if pw_doc:
        out["planned_workout_id"] = str(pw_doc["_id"])
        out["planned_workout_summary"] = {
            "id": str(pw_doc["_id"]),
            "title": pw_doc.get("title") or "Séance planifiée",
            "date": pw_doc.get("date"),
            "estimated_duration_s": pw_doc.get("estimated_duration_s", 0),
            "estimated_tss": pw_doc.get("estimated_tss", 0),
            "status": pw_doc.get("status", "done"),
            "compliance": pw_doc.get("compliance"),
        }
    else:
        out["planned_workout_summary"] = None

    return out


@router.get("/{activity_id}/streams")
async def get_activity_streams(
    org_id: str,
    activity_id: str,
    downsample: int = Query(default=1, ge=1, le=60),
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach", "athlete")),
):
    await _get_authorized_activity(org_id, activity_id, ctx)
    streams = await db_mod.activity_streams_col.find_one(
        {"activity_id": activity_id}, {"_id": 0, "org_id": 0, "uid": 0}
    )
    if not streams:
        return {
            "activity_id": activity_id,
            "t": [],
            "hr": [],
            "power": [],
            "cadence": [],
            "speed": [],
            "lat": [],
            "lng": [],
            "alt": [],
        }

    if downsample > 1:
        for key in ("t", "hr", "power", "cadence", "speed", "lat", "lng", "alt"):
            if isinstance(streams.get(key), list):
                streams[key] = streams[key][::downsample]
    return streams


@router.patch("/{activity_id}")
async def patch_activity(
    org_id: str,
    activity_id: str,
    body: ActivityPatchIn,
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach", "athlete")),
):
    await _get_authorized_activity(org_id, activity_id, ctx)
    updates: dict[str, Any] = {"updated_at": datetime.now(UTC)}
    for field in ("title", "sport", "rpe", "feeling", "notes"):
        val = getattr(body, field)
        if val is not None:
            updates[field] = val
    await db_mod.activities_col.update_one({"_id": activity_id}, {"$set": updates})
    updated = await db_mod.activities_col.find_one({"_id": activity_id})
    if updated and (body.rpe is not None or body.sport is not None):
        cur_metrics = updated.get("metrics") or {}
        if cur_metrics.get("tss") is None or cur_metrics.get("tss_method") == "rpe" or body.sport is not None:
            streams = await db_mod.activity_streams_col.find_one(
                {"activity_id": activity_id}, {"_id": 0}
            ) or {}
            thresholds = await resolve_thresholds_for_date(
                updated["uid"], updated["sport"], updated["start_time"]
            )
            new_metrics = compute_activity_metrics(
                streams,
                duration_s=float(updated.get("duration_s") or 0),
                distance_m=float(updated.get("distance_m") or 0),
                sport=updated["sport"],
                thresholds=thresholds,
                rpe=updated.get("rpe"),
            )
            await db_mod.activities_col.update_one(
                {"_id": activity_id},
                {"$set": {"metrics": new_metrics, "thresholds_snapshot": thresholds}},
            )
            updated["metrics"] = new_metrics
            updated["thresholds_snapshot"] = thresholds
            await recompute_pmc(org_id, updated["uid"])
    return {"activity": _serialize_activity(updated, include_laps=True)}


@router.delete("/{activity_id}")
async def delete_activity(
    org_id: str,
    activity_id: str,
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach", "athlete")),
):
    doc = await _get_authorized_activity(org_id, activity_id, ctx)
    await db_mod.activities_col.delete_one({"_id": activity_id})
    await db_mod.activity_streams_col.delete_one({"activity_id": activity_id})
    await db_mod.activity_comments_col.delete_many({"activity_id": activity_id})
    await db_mod.video_assets_col.delete_many({"activity_id": activity_id})
    await db_mod.planned_workouts_col.update_many(
        {"activity_id": activity_id},
        {"$set": {"activity_id": None, "compliance": None, "status": "planned"}},
    )
    await recompute_pmc(org_id, doc["uid"])
    return {"deleted": True, "id": activity_id}


@router.post("/{activity_id}/recalculate")
async def recalculate_activity(
    org_id: str,
    activity_id: str,
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach", "athlete")),
):
    doc = await _get_authorized_activity(org_id, activity_id, ctx)
    streams = await db_mod.activity_streams_col.find_one(
        {"activity_id": activity_id}, {"_id": 0}
    )
    if not streams:
        raise HTTPException(status_code=404, detail="Flux de l'activité introuvables.")

    thresholds = await resolve_thresholds_for_date(
        doc["uid"], doc["sport"], doc["start_time"]
    )
    metrics = compute_activity_metrics(
        streams,
        duration_s=float(doc.get("duration_s") or 0),
        distance_m=float(doc.get("distance_m") or 0),
        sport=doc["sport"],
        thresholds=thresholds,
    )
    await db_mod.activities_col.update_one(
        {"_id": activity_id},
        {
            "$set": {
                "thresholds_snapshot": thresholds,
                "metrics": metrics,
                "updated_at": datetime.now(UTC),
            }
        },
    )
    updated = await db_mod.activities_col.find_one({"_id": activity_id})
    await recompute_pmc(org_id, doc["uid"])
    return {"activity": _serialize_activity(updated, include_laps=True)}


@router.get("/{activity_id}/comments")
async def list_activity_comments(
    org_id: str,
    activity_id: str,
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach", "athlete")),
):
    await _get_authorized_activity(org_id, activity_id, ctx)
    docs = (
        await db_mod.activity_comments_col.find({"activity_id": activity_id})
        .sort("created_at", 1)
        .to_list(200)
    )
    return {
        "items": [
            {
                "id": str(d["_id"]),
                "activity_id": d["activity_id"],
                "author_uid": d["author_uid"],
                "author_name": d.get("author_name"),
                "author_role": d.get("author_role"),
                "body": d["body"],
                "created_at": d["created_at"].isoformat()
                if isinstance(d.get("created_at"), datetime)
                else d.get("created_at"),
            }
            for d in docs
        ]
    }


@router.patch("/{activity_id}/sharing")
async def update_activity_sharing(
    org_id: str,
    activity_id: str,
    body: ActivityShareIn,
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach", "athlete")),
):
    """P3 : Permet à l'athlète (ou admin) de partager une activité avec d'autres clubs/coachs."""
    doc = await _get_authorized_activity(org_id, activity_id, ctx)
    if doc.get("org_id") != org_id:
        raise HTTPException(
            status_code=403,
            detail="Le partage se configure depuis l'organisation d'origine de l'activité.",
        )
    if ctx.role == "coach" and doc.get("uid") != ctx.account.uid:
        raise HTTPException(status_code=403, detail="Seul l'athlète peut modifier le partage.")

    target_orgs = [oid for oid in dict.fromkeys(body.shared_org_ids) if oid != org_id]
    if target_orgs:
        valid_memberships = await db_mod.memberships_col.find(
            {
                "uid": doc["uid"],
                "org_id": {"$in": target_orgs},
                "status": "active",
            },
            {"org_id": 1},
        ).to_list(50)
        valid_org_ids = {m["org_id"] for m in valid_memberships}
        if len(valid_org_ids) != len(target_orgs):
            raise HTTPException(
                status_code=422,
                detail="Certaines organisations sélectionnées ne sont pas actives pour cet athlète.",
            )

    await db_mod.activities_col.update_one(
        {"_id": activity_id},
        {"$set": {"shared_org_ids": target_orgs, "updated_at": datetime.now(UTC)}},
    )
    updated = await db_mod.activities_col.find_one({"_id": activity_id})
    return {"activity": _serialize_activity(updated)}


@router.post("/{activity_id}/comments", status_code=201)
async def create_activity_comment(
    org_id: str,
    activity_id: str,
    body: ActivityCommentIn,
    ctx: OrgContext = Depends(require_org_role("owner", "admin", "coach", "athlete")),
):
    act = await _get_authorized_activity(org_id, activity_id, ctx)
    now = datetime.now(UTC)
    doc = {
        "_id": uuid.uuid4().hex,
        "activity_id": activity_id,
        "org_id": org_id,
        "author_uid": ctx.account.uid,
        "author_name": ctx.account.display_name,
        "author_role": ctx.role,
        "body": body.body,
        "created_at": now,
    }
    await db_mod.activity_comments_col.insert_one(doc)

    # P4 : Notifier l'athlète ou le staff concerné
    recipient_uids: list[str] = []
    if ctx.account.uid != act.get("uid") and act.get("uid"):
        recipient_uids.append(act["uid"])
    else:
        staff = await db_mod.memberships_col.find(
            {
                "org_id": org_id,
                "status": "active",
                "role": {"$in": ["owner", "admin", "coach"]},
                "uid": {"$ne": ctx.account.uid},
            },
            {"uid": 1},
        ).to_list(20)
        recipient_uids.extend(s["uid"] for s in staff)

    if recipient_uids:
        unique_recipients = list(dict.fromkeys(recipient_uids))
        notifs = [
            {
                "_id": uuid.uuid4().hex,
                "recipient_uid": r_uid,
                "org_id": org_id,
                "type": "activity_comment",
                "title": f"Nouveau commentaire de {ctx.account.display_name or 'Membre'}",
                "body": f"{act.get('title') or 'Activité'} : {body.body[:120]}",
                "link": f"/app/activities/{activity_id}",
                "read": False,
                "created_at": now,
            }
            for r_uid in unique_recipients
        ]
        await db_mod.notifications_col.insert_many(notifs)

        app_url = os.environ.get("APP_PUBLIC_URL", "https://app.videolog.app").rstrip("/")
        async for u in db_mod.users_col.find(
            {"_id": {"$in": unique_recipients}}, {"email": 1}
        ):
            if u.get("email"):
                await send_comment_notification_email(
                    to_email=u["email"],
                    author_name=ctx.account.display_name or "Membre",
                    item_title=act.get("title") or "Activité",
                    comment_body=body.body,
                    item_url=f"{app_url}/app/activities/{activity_id}",
                )

    return {
        "comment": {
            "id": doc["_id"],
            "activity_id": doc["activity_id"],
            "author_uid": doc["author_uid"],
            "author_name": doc["author_name"],
            "author_role": doc["author_role"],
            "body": doc["body"],
            "created_at": now.isoformat(),
        }
    }
