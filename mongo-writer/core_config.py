"""Configuration partagée et utilitaires LiveKit / enregistrements."""
from datetime import UTC, datetime
import logging
import os

from fastapi import HTTPException

from auth_firebase import Account, can_control_recording, can_join_room
import db as db_mod
from storage import get_storage

log = logging.getLogger(__name__)

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379")
LK_URL = os.environ.get("LIVEKIT_URL", "http://livekit:7880")
LK_KEY = os.environ["LIVEKIT_API_KEY"]
LK_SECRET = os.environ["LIVEKIT_API_SECRET"]
LK_PUB_URL = os.environ.get("LIVEKIT_PUBLIC_URL", "ws://10.0.2.2:7880")
MINIO_EP = os.environ.get("MINIO_ENDPOINT", "http://minio:9000")
MINIO_AK = os.environ["MINIO_ACCESS_KEY"]
MINIO_SK = os.environ["MINIO_SECRET_KEY"]
REC_BUCKET = os.environ.get("RECORDINGS_BUCKET", "recordings")
MINIO_PUBLIC_URL = os.environ["MINIO_PUBLIC_URL"]
RECORDING_MODE = os.environ.get("RECORDING_MODE", "manual").strip().lower()
RECORDING_MAX_DURATION_SECONDS = max(
    1, int(os.environ.get("RECORDING_MAX_DURATION_SECONDS", "180"))
)


async def get_authorized_room(
    room_name: str,
    account: Account,
    allow_finished: bool = False,
    require_control: bool = True,
) -> dict:
    room = await db_mod.rooms.find_one({"room_name": room_name}, {"_id": 0})
    if not room:
        raise HTTPException(status_code=404, detail="Session not found.")
    if room.get("finished") and not allow_finished:
        raise HTTPException(status_code=409, detail="Session has already finished.")

    allowed = (
        can_control_recording(account, room)
        if require_control
        else can_join_room(account, room)
    )
    if not allowed:
        raise HTTPException(
            status_code=403,
            detail="Vous n'êtes pas autorisé à accéder à cette session.",
        )
    return room


def require_manual_recording_mode() -> None:
    if RECORDING_MODE != "manual":
        raise HTTPException(
            status_code=409,
            detail="Mobile recording control requires RECORDING_MODE=manual.",
        )


async def get_max_recording_seconds(room_doc: dict | None) -> int:
    org_id = (room_doc or {}).get("org_id")
    if org_id:
        try:
            org = await db_mod.orgs_col.find_one(
                {"_id": org_id}, {"settings.max_recording_s": 1}
            )
            custom = ((org or {}).get("settings") or {}).get("max_recording_s")
            if isinstance(custom, (int, float)) and custom >= 60:
                return int(custom)
        except Exception as exc:
            log.warning("Could not read org max_recording_s for %s: %s", org_id, exc)
    return RECORDING_MAX_DURATION_SECONDS


def sign_recording_files(doc: dict) -> dict:
    storage = get_storage()
    for item in doc.get("files", []):
        filename = item.get("filename", "").lstrip("/")
        if filename:
            signed_url = storage.presign_get(
                filename, expires_in=3600, bucket=REC_BUCKET
            )
            item["url"] = signed_url
            item["minio_url"] = signed_url
    return doc


def livekit_timestamp_iso(value) -> str | None:
    try:
        timestamp_ns = int(value)
        if timestamp_ns <= 0:
            raise ValueError("LiveKit timestamp is missing")
        return datetime.fromtimestamp(timestamp_ns / 1_000_000_000, UTC).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def epoch_ms(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.timestamp() * 1000
    except (TypeError, ValueError, OverflowError):
        return None


def egress_status(code) -> str:
    try:
        from livekit.protocol import egress as proto_egress

        return proto_egress.EgressStatus.Name(code)
    except Exception:
        return str(code)
