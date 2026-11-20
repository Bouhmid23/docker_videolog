"""Route /token : génération de JWT LiveKit pour les participants autorisés."""
from datetime import UTC, datetime, timedelta
import json
import logging

from fastapi import APIRouter, Depends, HTTPException

from auth_firebase import Account, can_join_room, current_account
from core_config import LK_KEY, LK_PUB_URL, LK_SECRET
import db as db_mod
from routers.live_rooms import _parse_utc_dt, is_room_joinable

log = logging.getLogger(__name__)
router = APIRouter(tags=["livekit-token"])


@router.post("/token")
async def generate_token(body: dict, account: Account = Depends(current_account)):
    from livekit import api as lkapi

    room = body.get("room")
    if not isinstance(room, str) or not room.strip() or len(room) > 128:
        raise HTTPException(status_code=422, detail="A valid room is required.")
    room = room.strip()
    room_doc = await db_mod.rooms.find_one({"room_name": room}, {"_id": 0})
    if not room_doc or room_doc.get("finished"):
        raise HTTPException(status_code=404, detail="Session not found.")

    joinable, reason = is_room_joinable(room_doc)
    if not joinable:
        raise HTTPException(status_code=410, detail=reason or "Session expirée.")

    if not can_join_room(account, room_doc):
        raise HTTPException(
            status_code=403,
            detail="Vous n'êtes pas autorisé à rejoindre cette session.",
        )

    now = datetime.now(UTC)
    end_dt = _parse_utc_dt(room_doc.get("scheduled_end_at"))
    if end_dt and end_dt > now:
        ttl_s = max(600, int((end_dt - now).total_seconds()) + 1800)
    else:
        ttl_s = max(600, int(room_doc.get("duration_s") or 3600))

    uid = account.uid
    org_id = room_doc.get("org_id")
    membership = account.membership(org_id) if org_id else None
    lk_role = (
        membership.role
        if membership
        else ("platform_admin" if account.platform_admin else "guest")
    )

    identity = f"{uid}:web" if body.get("client") == "web" else uid
    try:
        token = (
            lkapi.AccessToken(LK_KEY, LK_SECRET)
            .with_identity(identity)
            .with_name(account.display_name)
            .with_metadata(json.dumps({"role": lk_role, "org_id": org_id}))
            .with_ttl(timedelta(seconds=ttl_s))
            .with_grants(
                lkapi.VideoGrants(
                    room_join=True,
                    room=room,
                    can_publish=True,
                    can_subscribe=True,
                )
            )
            .to_jwt()
        )
        return {"token": token, "room": room, "identity": identity, "url": LK_PUB_URL}
    except Exception as error:
        log.exception("Failed to issue LiveKit token for Firebase user %s", uid)
        raise HTTPException(
            status_code=502, detail="Unable to issue a LiveKit token."
        ) from error
