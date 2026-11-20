"""Worker Arq : file de tâches asynchrones (finalisation de session, ingestion, métriques)."""
from datetime import UTC, datetime
import logging
import os

from arq.connections import RedisSettings

import db as db_mod
from routers.thresholds import _recalculate_since
from services.live_to_activity import convert_room_to_activities
from services.pmc import recompute_pmc

log = logging.getLogger(__name__)
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379")


async def finalize_live_session(ctx: dict, room_name: str) -> dict:
    """Finalise une session live et la convertit en activités par participant."""
    room = await db_mod.rooms.find_one({"room_name": room_name}, {"_id": 0})
    if not room:
        log.warning("finalize_live_session: room %s not found", room_name)
        return {"room_name": room_name, "status": "not_found"}

    telemetry_count = await db_mod.col.count_documents({"room_name": room_name})
    recordings_count = await db_mod.recs.count_documents({"room_name": room_name})
    now = datetime.now(UTC)

    await db_mod.rooms.update_one(
        {"room_name": room_name},
        {
            "$set": {
                "finalized_at": now,
                "telemetry_sample_count": telemetry_count,
                "recordings_count": recordings_count,
            }
        },
    )
    converted = await convert_room_to_activities(room_name)
    return {
        "room_name": room_name,
        "status": "finalized",
        "telemetry_sample_count": telemetry_count,
        "recordings_count": recordings_count,
        "converted_activities": len(converted),
    }


async def recalculate_athlete_activities(
    ctx: dict, uid: str, org_id: str, sport: str, since_iso: str
) -> dict:
    updated = await _recalculate_since(uid, org_id, sport, since_iso)
    await recompute_pmc(org_id, uid)
    return {"uid": uid, "org_id": org_id, "sport": sport, "updated": updated}


async def recompute_athlete_pmc(ctx: dict, org_id: str, uid: str) -> dict:
    series = await recompute_pmc(org_id, uid)
    return {"org_id": org_id, "uid": uid, "days": len(series)}


async def startup(ctx: dict) -> None:
    await db_mod.ensure_indexes()
    log.info("Arq worker started")


async def shutdown(ctx: dict) -> None:
    log.info("Arq worker shutting down")


class WorkerSettings:
    functions = [finalize_live_session, recalculate_athlete_activities, recompute_athlete_pmc]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(REDIS_URL)
