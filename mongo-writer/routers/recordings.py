"""Routes d'enregistrement Egress (mobile, opérateur et webhook LiveKit)."""
import asyncio
from datetime import UTC, datetime, timedelta
import json
import logging
from typing import Literal
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request

from auth_firebase import Account, current_account
from core_config import (
    LK_KEY,
    LK_SECRET,
    LK_URL,
    MINIO_AK,
    MINIO_EP,
    MINIO_PUBLIC_URL,
    MINIO_SK,
    REC_BUCKET,
    egress_status,
    get_authorized_room,
    get_max_recording_seconds,
    livekit_timestamp_iso,
    require_manual_recording_mode,
    sign_recording_files,
)
import db as db_mod

log = logging.getLogger(__name__)
router = APIRouter(tags=["recordings"])


@router.post("/mobile/rooms/{room_name}/recording/start")
async def mobile_start_recording(
    room_name: str,
    layout: Literal["grid", "speaker", "single-speaker"] = "grid",
    account: Account = Depends(current_account),
):
    from livekit import api as lkapi
    from livekit.protocol import egress as proto_egress

    require_manual_recording_mode()
    room = await get_authorized_room(room_name, account, require_control=True)
    max_duration_s = await get_max_recording_seconds(room)

    active = await db_mod.recs.find_one(
        {
            "room_name": room_name,
            "status": {
                "$in": [
                    "recording",
                    "EGRESS_STARTING",
                    "EGRESS_ACTIVE",
                    "EGRESS_ENDING",
                ]
            },
        },
        {"_id": 0, "egress_id": 1},
    )
    if active:
        raise HTTPException(
            status_code=409,
            detail="An Egress recording is already active for this session.",
        )

    request_id = uuid.uuid4().hex
    requested_at = datetime.now(UTC)
    reservation = await db_mod.rooms.update_one(
        {
            "room_name": room_name,
            "finished": {"$ne": True},
            "$or": [
                {
                    "mobile_recording.status": {
                        "$in": [
                            "completed",
                            "failed",
                            "stopped",
                            "EGRESS_COMPLETE",
                            "EGRESS_FAILED",
                            "EGRESS_ABORTED",
                        ]
                    }
                },
                {"mobile_recording": {"$exists": False}},
            ],
        },
        {
            "$set": {
                "mobile_recording": {
                    "request_id": request_id,
                    "status": "starting",
                    "requested_by": account.uid,
                    "requested_at": requested_at,
                }
            }
        },
    )
    if reservation.modified_count != 1:
        raise HTTPException(
            status_code=409,
            detail="A recording start or stop is already in progress.",
        )

    timestamp = requested_at.strftime("%Y%m%d_%H%M%S")
    lk = None
    egress_id = None
    try:
        lk = lkapi.LiveKitAPI(LK_URL, LK_KEY, LK_SECRET)
        req = proto_egress.RoomCompositeEgressRequest(
            room_name=room_name,
            layout=layout,
            audio_only=False,
            video_only=False,
            file=proto_egress.EncodedFileOutput(
                file_type=1,
                filepath=f"{room_name}/{timestamp}/recording",
                s3=proto_egress.S3Upload(
                    access_key=MINIO_AK,
                    secret=MINIO_SK,
                    bucket=REC_BUCKET,
                    endpoint=MINIO_EP,
                    region="us-east-1",
                    force_path_style=True,
                ),
            ),
            advanced=proto_egress.EncodingOptions(
                width=1280,
                height=720,
                depth=24,
                framerate=24,
                audio_codec=2,
                audio_bitrate=128,
                audio_frequency=44100,
                video_codec=1,
                video_bitrate=2500,
                key_frame_interval=4,
            ),
        )
        info = await lk.egress.start_room_composite_egress(req)
        egress_id = info.egress_id
        status = egress_status(info.status)
        actual_started_at = datetime.now(UTC)
        stop_deadline_at = actual_started_at + timedelta(seconds=max_duration_s)
        await db_mod.recs.update_one(
            {"egress_id": info.egress_id},
            {
                "$set": {
                    "room_name": room_name,
                    "requested_by": account.uid,
                    "source": "mobile",
                    "requested_at": requested_at.isoformat(),
                    "stop_deadline_at": stop_deadline_at,
                    "max_duration_seconds": max_duration_s,
                },
                "$setOnInsert": {
                    "egress_id": info.egress_id,
                    "status": status,
                    "files": [],
                },
            },
            upsert=True,
        )
        await db_mod.rooms.update_one(
            {"room_name": room_name, "mobile_recording.request_id": request_id},
            {
                "$set": {
                    "mobile_recording.egress_id": info.egress_id,
                    "mobile_recording.status": status,
                    "mobile_recording.stop_deadline_at": stop_deadline_at,
                }
            },
        )
        latest = await db_mod.recs.find_one(
            {"egress_id": info.egress_id}, {"_id": 0, "status": 1}
        )
        if latest and latest.get("status"):
            await db_mod.rooms.update_one(
                {"room_name": room_name, "mobile_recording.request_id": request_id},
                {"$set": {"mobile_recording.status": latest["status"]}},
            )
        return {
            "room_name": room_name,
            "egress_id": info.egress_id,
            "status": status,
            "max_duration_seconds": max_duration_s,
            "stop_deadline_at": stop_deadline_at,
        }
    except Exception as error:
        if egress_id and lk:
            try:
                await lk.egress.stop_egress(
                    proto_egress.StopEgressRequest(egress_id=egress_id)
                )
            except Exception:
                log.exception(
                    "Failed to roll back Egress %s after persistence error", egress_id
                )
        await db_mod.rooms.update_one(
            {"room_name": room_name, "mobile_recording.request_id": request_id},
            {"$set": {"mobile_recording.status": "failed"}},
        )
        log.exception("Mobile Egress start failed for room %s", room_name)
        if isinstance(error, HTTPException):
            raise
        raise HTTPException(
            status_code=502,
            detail="LiveKit could not start the recording.",
        ) from error
    finally:
        if lk:
            await lk.aclose()


@router.post("/mobile/rooms/{room_name}/recording/stop")
async def mobile_stop_recording(
    room_name: str, account: Account = Depends(current_account)
):
    from livekit import api as lkapi
    from livekit.protocol import egress as proto_egress

    require_manual_recording_mode()
    room = await get_authorized_room(room_name, account, require_control=True)
    control = room.get("mobile_recording") or {}
    egress_id = control.get("egress_id")
    if not egress_id:
        raise HTTPException(status_code=409, detail="No mobile recording is active.")

    recording = await db_mod.recs.find_one(
        {"egress_id": egress_id, "room_name": room_name},
        {"_id": 0, "status": 1},
    )
    if not recording or recording.get("status") in {
        "completed",
        "failed",
        "stopped",
        "EGRESS_COMPLETE",
        "EGRESS_FAILED",
        "EGRESS_ABORTED",
    }:
        raise HTTPException(status_code=409, detail="No active recording was found.")

    previous_status = control.get("status", "recording")
    stop_reservation = await db_mod.rooms.update_one(
        {
            "room_name": room_name,
            "mobile_recording.egress_id": egress_id,
            "mobile_recording.status": {
                "$nin": [
                    "stopping",
                    "completed",
                    "failed",
                    "stopped",
                    "EGRESS_ENDING",
                    "EGRESS_COMPLETE",
                    "EGRESS_FAILED",
                    "EGRESS_ABORTED",
                ]
            },
        },
        {"$set": {"mobile_recording.status": "stopping"}},
    )
    if stop_reservation.modified_count != 1:
        raise HTTPException(
            status_code=409, detail="The recording is already stopping."
        )

    lk = None
    stop_requested_at = datetime.now(UTC)
    try:
        await db_mod.recs.update_one(
            {"egress_id": egress_id},
            {
                "$set": {
                    "mobile_stop_requested_at": stop_requested_at,
                    "stop_reason": "mobile",
                }
            },
        )
        lk = lkapi.LiveKitAPI(LK_URL, LK_KEY, LK_SECRET)
        info = await lk.egress.stop_egress(
            proto_egress.StopEgressRequest(egress_id=egress_id)
        )
        await db_mod.rooms.update_one(
            {"room_name": room_name, "mobile_recording.egress_id": egress_id},
            {
                "$set": {
                    "mobile_recording.stopped_by": account.uid,
                    "mobile_recording.stop_requested_at": datetime.now(UTC),
                }
            },
        )
        return {
            "room_name": room_name,
            "egress_id": info.egress_id,
            "status": egress_status(info.status),
        }
    except Exception as error:
        await db_mod.recs.update_one(
            {"egress_id": egress_id},
            {"$unset": {"mobile_stop_requested_at": "", "stop_reason": ""}},
        )
        await db_mod.rooms.update_one(
            {
                "room_name": room_name,
                "mobile_recording.egress_id": egress_id,
                "mobile_recording.status": "stopping",
            },
            {"$set": {"mobile_recording.status": previous_status}},
        )
        log.exception("Mobile Egress stop failed for room %s", room_name)
        raise HTTPException(
            status_code=502,
            detail="LiveKit could not stop the recording.",
        ) from error
    finally:
        if lk:
            await lk.aclose()


@router.get("/mobile/rooms/{room_name}/recording")
async def mobile_recording_status(
    room_name: str, account: Account = Depends(current_account)
):
    room = await get_authorized_room(
        room_name, account, allow_finished=True, require_control=False
    )
    control = room.get("mobile_recording") or {}
    egress_id = control.get("egress_id")
    if not egress_id:
        return {"room_name": room_name, "status": control.get("status", "not_started")}

    recording = await db_mod.recs.find_one({"egress_id": egress_id}, {"_id": 0})
    if not recording:
        return {
            "room_name": room_name,
            "egress_id": egress_id,
            "status": control.get("status", "starting"),
        }
    sign_recording_files(recording)
    return recording


@router.post("/webhook/livekit")
async def livekit_webhook(request: Request):
    from livekit import api as lkapi

    raw_body = await request.body()
    auth_header = request.headers.get("authorization")
    if not auth_header:
        raise HTTPException(
            status_code=401, detail="Missing LiveKit webhook authorization."
        )
    try:
        receiver = lkapi.WebhookReceiver(lkapi.TokenVerifier(LK_KEY, LK_SECRET))
        await asyncio.to_thread(
            receiver.receive, raw_body.decode("utf-8"), auth_header
        )
        body = json.loads(raw_body)
    except Exception as error:
        log.warning("Rejected invalid LiveKit webhook: %s", error)
        raise HTTPException(
            status_code=401, detail="Invalid LiveKit webhook signature."
        ) from error

    r = getattr(request.app.state, "redis", None)
    event = body.get("event", "")
    egress_info = body.get("egressInfo", {})
    if not egress_info:
        return {"ok": True}
    egress_id = egress_info.get("egressId", "")
    room_name = egress_info.get("roomName", "")
    if event == "egress_started":
        await db_mod.recs.update_one(
            {"egress_id": egress_id},
            {
                "$set": {
                    "egress_id": egress_id,
                    "room_name": room_name,
                    "status": "recording",
                    "started_at": livekit_timestamp_iso(egress_info.get("startedAt")),
                    "files": [],
                }
            },
            upsert=True,
        )
        await db_mod.rooms.update_one(
            {"room_name": room_name, "mobile_recording.egress_id": egress_id},
            {"$set": {"mobile_recording.status": "recording"}},
        )
        if r:
            await r.publish(
                "egress:events",
                json.dumps(
                    {"event": "started", "egress_id": egress_id, "room": room_name}
                ),
            )
    elif event == "egress_ended":
        file_results = egress_info.get("fileResults") or []
        files = []
        for f in file_results:
            fname = f.get("filename", "")
            try:
                fsize = int(f.get("size", 0) or 0)
            except (TypeError, ValueError):
                fsize = 0
            raw_dur = f.get("duration", 0) or 0
            try:
                dur_s = float(raw_dur)
                if dur_s > 100_000_000:
                    dur_s = round(dur_s / 1_000_000_000, 2)
            except (TypeError, ValueError):
                dur_s = 0.0
            furl = f"{MINIO_PUBLIC_URL}/{REC_BUCKET}/{str(fname).lstrip('/')}"
            files.append(
                {
                    "filename": fname,
                    "size": fsize,
                    "size_bytes": fsize,
                    "size_mb": round(fsize / (1024 * 1024), 2),
                    "duration": dur_s,
                    "duration_s": dur_s,
                    "url": furl,
                    "minio_url": furl,
                }
            )
        duration_s = max((f["duration_s"] for f in files), default=None)
        error = egress_info.get("error", "")
        update = {
            "status": "failed" if error else "completed",
            "ended_at": livekit_timestamp_iso(egress_info.get("endedAt")),
            "duration_s": duration_s,
            "files": files,
            "error": error or None,
        }
        if file_results:
            update["video_started_at"] = livekit_timestamp_iso(
                file_results[0].get("startedAt")
            )
            update["video_ended_at"] = livekit_timestamp_iso(
                file_results[0].get("endedAt")
            )
        await db_mod.recs.update_one({"egress_id": egress_id}, {"$set": update}, upsert=True)
        await db_mod.rooms.update_one(
            {"room_name": room_name, "mobile_recording.egress_id": egress_id},
            {"$set": {"mobile_recording.status": update["status"]}},
        )
        if r:
            await r.publish(
                "egress:events",
                json.dumps(
                    {
                        "event": "ended",
                        "egress_id": egress_id,
                        "status": "failed" if error else "completed",
                        "files": files,
                    }
                ),
            )
    elif event == "egress_updated":
        status = egress_info.get("status", "")
        await db_mod.recs.update_one(
            {"egress_id": egress_id},
            {"$set": {"status": status, "updated_at": datetime.now(UTC).isoformat()}},
            upsert=True,
        )
        await db_mod.rooms.update_one(
            {"room_name": room_name, "mobile_recording.egress_id": egress_id},
            {"$set": {"mobile_recording.status": status}},
        )
    return {"ok": True}
