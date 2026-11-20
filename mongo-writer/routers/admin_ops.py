"""Routes de supervision, télémétrie brute et SSE opérateur."""
import asyncio
import json
import logging
import time
from typing import AsyncGenerator
from urllib.error import URLError
from urllib.request import urlopen

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from redis.exceptions import RedisError

from auth_firebase import Account, current_account
from core_config import MINIO_EP
import db as db_mod

log = logging.getLogger(__name__)
router = APIRouter(tags=["admin-ops"])


@router.get("/egress/status")
async def egress_status(
    request: Request,
    _account: Account = Depends(current_account),
):
    """Vérifie si Egress est disponible dans Redis (diagnostique, via SCAN non bloquant)."""
    r = getattr(request.app.state, "redis", None)
    if not r:
        return {"egress_keys_in_redis": [], "registered": False}
    try:
        found: set[str] = set()
        for pattern in ("*gress*", "*Egress*"):
            async for key in r.scan_iter(match=pattern, count=200):
                found.add(key)
        keys = sorted(found)
        return {
            "egress_keys_in_redis": keys,
            "registered": len(keys) > 0,
            "note": "Si registered=false, le container vl-egress n'est pas démarré ou a crashé",
        }
    except Exception as e:
        return {"error": str(e), "registered": False}


@router.get("/health")
async def health():
    return {"status": "ok", "ts": time.time()}


@router.get("/minio/status")
async def minio_status(_account: Account = Depends(current_account)):
    health_url = f"{MINIO_EP.rstrip('/')}/minio/health/live"
    try:
        response = await asyncio.to_thread(urlopen, health_url, timeout=3)
        response.close()
    except (URLError, TimeoutError, OSError) as error:
        log.warning("MinIO health check failed: %s", error)
        return {"status": "unavailable", "detail": str(error)}
    return {"status": "ok"}


@router.get("/stats")
async def stats(
    request: Request,
    room: str | None = None,
    _account: Account = Depends(current_account),
):
    query = {"room_name": room} if room else {}
    total = await db_mod.col.count_documents(query)
    session = await db_mod.sess.find_one(query, sort=[("started_at", -1)])
    stream_len = None
    stream_pending = None
    stream_lag = None
    writer_group_found = False
    r = getattr(request.app.state, "redis", None)
    if r:
        stream_len = await r.xlen("telemetry:stream")
        try:
            groups = await r.xinfo_groups("telemetry:stream")
            writer_group = next(
                (group for group in groups if group.get("name") == "mongo-writers"),
                None,
            )
            if writer_group:
                writer_group_found = True
                stream_pending = writer_group.get("pending", 0)
                stream_lag = writer_group.get("lag")
        except RedisError as error:
            log.warning("Unable to inspect telemetry writer group: %s", error)
    alert_cnt = await db_mod.alerts.count_documents(query)
    last = await db_mod.col.find_one(query, sort=[("ts", -1)])
    return {
        "total_events": total,
        "stream_length": stream_len,
        "stream_pending": stream_pending,
        "stream_lag": stream_lag,
        "writer_group_found": writer_group_found,
        "alert_count": alert_cnt,
        "last_event_ts": last["ts"] if last else None,
        "session": {
            "room_name": session["room_name"] if session else None,
            "started_at": session["started_at"] if session else None,
        }
        if session
        else None,
    }


@router.get("/alerts")
async def get_alerts(
    room: str = "poc-room",
    limit: int = 50,
    role: str | None = None,
    _account: Account = Depends(current_account),
):
    query = {"room_name": room}
    if role:
        telemetry_query = {"room_name": room}
        if role == "trainee":
            telemetry_query["$or"] = [
                {"telemetry.role": "trainee"},
                {"telemetry.role": None},
            ]
        else:
            telemetry_query["telemetry.role"] = role
        participants = await db_mod.col.distinct("participant", telemetry_query)
        if not participants:
            return []
        query["participant"] = {"$in": participants}
    return (
        await db_mod.alerts.find(query, {"_id": 0}, sort=[("ts", -1)], limit=limit)
        .to_list(limit)
    )


@router.get("/events")
async def sse_events(
    request: Request,
    room: str | None = None,
    _account: Account = Depends(current_account),
):
    """Server-Sent Events — pousse les updates en temps réel au dashboard."""
    r = getattr(request.app.state, "redis", None)

    async def event_generator() -> AsyncGenerator[str, None]:
        pubsub = None
        try:
            yield "event: ping\ndata: {}\n\n"
            last_ts = 0.0
            tick = 0
            last_ping = time.monotonic()
            next_subscribe = 0.0

            while True:
                if await request.is_disconnected():
                    break

                now = time.monotonic()
                if pubsub is None and now >= next_subscribe and r is not None:
                    candidate = None
                    try:
                        candidate = r.pubsub()
                        await asyncio.wait_for(
                            candidate.subscribe(
                                "alerts", "egress:events", "room:events"
                            ),
                            timeout=5,
                        )
                        pubsub = candidate
                    except (RedisError, OSError, TimeoutError) as error:
                        log.warning("SSE Redis subscription failed: %s", error)
                        if candidate:
                            try:
                                await candidate.aclose()
                            except RedisError as close_error:
                                log.warning("SSE Redis cleanup failed: %s", close_error)
                        next_subscribe = time.monotonic() + 5

                if pubsub:
                    try:
                        msg = await pubsub.get_message(
                            ignore_subscribe_messages=True, timeout=0.05
                        )
                    except (RedisError, OSError, TimeoutError) as error:
                        log.warning("SSE Redis connection lost: %s", error)
                        try:
                            await pubsub.aclose()
                        except RedisError as close_error:
                            log.warning("SSE Redis cleanup failed: %s", close_error)
                        pubsub = None
                        next_subscribe = time.monotonic() + 1
                        msg = None
                else:
                    msg = None

                if msg and msg["type"] == "message":
                    channel = msg["channel"]
                    try:
                        data = json.loads(msg["data"])
                    except Exception:
                        data = {"raw": msg["data"]}

                    event_room = data.get("room_name") or data.get("room")
                    if room and event_room and event_room != room:
                        await asyncio.sleep(0.05)
                        continue

                    if channel == "alerts":
                        yield f"event: alert\ndata: {json.dumps(data)}\n\n"
                    elif channel == "egress:events":
                        yield f"event: egress\ndata: {json.dumps(data)}\n\n"
                    elif channel == "room:events":
                        yield f"event: room\ndata: {json.dumps(data)}\n\n"

                tick += 1
                if tick % 10 == 0:
                    query = {"room_name": room} if room else {}
                    last = await db_mod.col.find_one(
                        query, {"_id": 0}, sort=[("ts", -1)]
                    )
                    if last and last.get("ts", 0) != last_ts:
                        last_ts = last.get("ts", 0)
                        t = last.get("telemetry", {})
                        payload = {
                            "room_name": last.get("room_name", ""),
                            "pts_ms": last.get("pts_ms", 0),
                            "participant": last.get("participant", ""),
                            "role": t.get("role", "trainee"),
                            "speed": t.get("speed", 0),
                            "heart_rate": t.get("heart_rate", 0),
                            "cadence": t.get("cadence", 0),
                            "rpm": t.get("rpm", 0),
                            "gear": t.get("gear", 0),
                            "brake": t.get("brake", 0),
                        }
                        yield f"event: telemetry\ndata: {json.dumps(payload)}\n\n"

                if time.monotonic() - last_ping >= 15:
                    yield "event: ping\ndata: {}\n\n"
                    last_ping = time.monotonic()

                await asyncio.sleep(0.05)

        except asyncio.CancelledError:
            raise
        finally:
            if pubsub:
                try:
                    await pubsub.unsubscribe()
                except RedisError as error:
                    log.warning("SSE Redis unsubscribe failed: %s", error)
                try:
                    await pubsub.aclose()
                except RedisError as error:
                    log.warning("SSE Redis cleanup failed: %s", error)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
