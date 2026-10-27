"""
Dashboard API — Videolog POC
FastAPI avec Server-Sent Events (SSE) pour le dashboard temps réel.
"""
import asyncio, json, logging, os, time, uuid
from datetime import datetime, UTC, timedelta
from typing import AsyncGenerator, Any, Literal
from urllib.error import URLError
from urllib.request import urlopen

import redis.asyncio as aioredis
from redis.exceptions import RedisError
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.openapi.docs import get_swagger_ui_html, get_swagger_ui_oauth2_redirect_html
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field

MONGO_URL  = os.environ.get("MONGO_URL",         "mongodb://mongo:27017/videolog")
REDIS_URL  = os.environ.get("REDIS_URL",         "redis://redis:6379")
LK_URL     = os.environ.get("LIVEKIT_URL",       "http://livekit:7880")
LK_KEY     = os.environ["LIVEKIT_API_KEY"]
LK_SECRET  = os.environ["LIVEKIT_API_SECRET"]
LK_PUB_URL = os.environ.get("LIVEKIT_PUBLIC_URL","ws://10.0.2.2:7880")
MINIO_EP   = os.environ.get("MINIO_ENDPOINT",    "http://minio:9000")
MINIO_AK   = os.environ["MINIO_ACCESS_KEY"]
MINIO_SK   = os.environ["MINIO_SECRET_KEY"]
REC_BUCKET = "recordings"
MINIO_PUBLIC_URL = os.environ["MINIO_PUBLIC_URL"]
FIREBASE_PROJECT_ID = os.environ.get("FIREBASE_PROJECT_ID")
FIREBASE_SERVICE_ACCOUNT_FILE = os.environ.get(
    "FIREBASE_SERVICE_ACCOUNT_FILE",
    "/run/secrets/firebase/firebase-service-account.json",
)
RECORDING_MODE = os.environ.get("RECORDING_MODE", "manual").strip().lower()
RECORDING_MAX_DURATION_SECONDS = max(
    1, int(os.environ.get("RECORDING_MAX_DURATION_SECONDS", "180"))
)

app = FastAPI(title="Videolog Dashboard API", docs_url=None)
mongo  = AsyncIOMotorClient(MONGO_URL)
col    = mongo.videolog.telemetry
recs   = mongo.videolog.recordings
sess   = mongo.videolog.sessions
alerts = mongo.videolog.alerts
rooms  = mongo.videolog.room_state
r: aioredis.Redis = None
log = logging.getLogger(__name__)
bearer_scheme = HTTPBearer(auto_error=False)


class RemoveParticipantBody(BaseModel):
    identity: str = Field(min_length=1, max_length=128)


def _firebase_app():
    import firebase_admin
    from firebase_admin import credentials

    try:
        return firebase_admin.get_app()
    except ValueError:
        if not os.path.isfile(FIREBASE_SERVICE_ACCOUNT_FILE):
            raise HTTPException(
                status_code=503,
                detail="Firebase Authentication is not configured on the server.",
            )
        options = {"projectId": FIREBASE_PROJECT_ID} if FIREBASE_PROJECT_ID else None
        try:
            return firebase_admin.initialize_app(
                credentials.Certificate(FIREBASE_SERVICE_ACCOUNT_FILE),
                options=options,
            )
        except Exception as error:
            log.exception("Firebase Admin SDK initialization failed")
            raise HTTPException(
                status_code=503,
                detail="Firebase Authentication is unavailable.",
            ) from error


async def firebase_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="A Firebase ID token is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    from firebase_admin import auth

    firebase_app = _firebase_app()
    try:
        claims = await asyncio.to_thread(
            auth.verify_id_token,
            credentials.credentials,
            check_revoked=True,
            app=firebase_app,
        )
    except (auth.InvalidIdTokenError, auth.ExpiredIdTokenError, auth.RevokedIdTokenError) as error:
        raise HTTPException(
            status_code=401,
            detail="The Firebase ID token is invalid or expired.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from error
    except Exception as error:
        log.exception("Firebase ID token verification failed")
        raise HTTPException(
            status_code=503,
            detail="Firebase Authentication is temporarily unavailable.",
        ) from error

    if not claims.get("uid"):
        raise HTTPException(status_code=401, detail="Firebase token has no user identity.")
    return claims


async def _get_authorized_room(
    room_name: str, user: dict, allow_finished: bool = False
) -> dict:
    room = await rooms.find_one({"room_name": room_name}, {"_id": 0})
    if not room:
        raise HTTPException(status_code=404, detail="Session not found.")
    if room.get("finished") and not allow_finished:
        raise HTTPException(status_code=409, detail="Session has already finished.")

    # POC behavior: every authenticated Firebase user may control Egress.
    return room


def _require_manual_recording_mode():
    if RECORDING_MODE != "manual":
        raise HTTPException(
            status_code=409,
            detail="Mobile recording control requires RECORDING_MODE=manual.",
        )


@app.get("/docs", include_in_schema=False)
async def api_docs():
    return get_swagger_ui_html(
        openapi_url="/api/openapi.json",
        title=f"{app.title} - Swagger UI",
        oauth2_redirect_url="/api/docs/oauth2-redirect",
    )


@app.get("/docs/oauth2-redirect", include_in_schema=False)
async def api_docs_oauth_redirect():
    return get_swagger_ui_oauth2_redirect_html()


def _minio_client(public: bool = False):
    from minio import Minio
    from urllib.parse import urlparse
    endpoint = MINIO_PUBLIC_URL if public else MINIO_EP
    parsed = urlparse(endpoint)
    return Minio(parsed.netloc, access_key=MINIO_AK, secret_key=MINIO_SK,
                 secure=parsed.scheme == "https")


def _sign_recording_files(doc: dict) -> dict:
    from minio import Minio
    client = _minio_client(public=True)
    for item in doc.get("files", []):
        filename = item.get("filename", "").lstrip("/")
        if filename:
            signed_url = client.presigned_get_object(
                REC_BUCKET, filename, expires=timedelta(hours=1))
            item["url"] = signed_url
            item["minio_url"] = signed_url
    return doc


def _livekit_timestamp_iso(value) -> str | None:
    try:
        timestamp_ns = int(value)
        if timestamp_ns <= 0:
            raise ValueError("LiveKit timestamp is missing")
        return datetime.fromtimestamp(timestamp_ns / 1_000_000_000, UTC).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _epoch_ms(value) -> float | None:
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


@app.on_event("startup")
async def startup():
    global r
    r = aioredis.from_url(REDIS_URL, decode_responses=True)


# ── Health ─────────────────────────────────────────────────────────────────────


@app.get("/egress/status")
async def egress_status():
    """Vérifie si Egress est disponible dans Redis (diagnostique)."""
    try:
        # Egress workers s'enregistrent dans Redis sous la clé EgressInternal:*
        # Utiliser le client aioredis déjà initialisé (évite de bloquer la boucle d'événements)
        keys = await r.keys("*gress*")
        keys += await r.keys("*Egress*")
        return {
            "egress_keys_in_redis": list(keys),
            "registered": len(keys) > 0,
            "note": "Si registered=false, le container vl-egress n'est pas démarré ou a crashé"
        }
    except Exception as e:
        return {"error": str(e), "registered": False}

@app.get("/health")
async def health():
    return {"status": "ok", "ts": time.time()}


@app.get("/minio/status")
async def minio_status():
    health_url = f"{MINIO_EP.rstrip('/')}/minio/health/live"
    try:
        response = await asyncio.to_thread(urlopen, health_url, timeout=3)
        response.close()
    except (URLError, TimeoutError, OSError) as error:
        log.warning("MinIO health check failed: %s", error)
        return {"status": "unavailable", "detail": str(error)}
    return {"status": "ok"}


# ── Stats ──────────────────────────────────────────────────────────────────────

@app.get("/stats")
async def stats(room: str | None = None):
    query      = {"room_name": room} if room else {}
    total      = await col.count_documents(query)
    session    = await sess.find_one(query, sort=[("started_at", -1)])
    stream_len = None
    stream_pending = None
    stream_lag = None
    writer_group_found = False
    if r:
        stream_len = await r.xlen("telemetry:stream")
        try:
            groups = await r.xinfo_groups("telemetry:stream")
            writer_group = next(
                (group for group in groups
                 if group.get("name") == "mongo-writers"),
                None,
            )
            if writer_group:
                writer_group_found = True
                stream_pending = writer_group.get("pending", 0)
                stream_lag = writer_group.get("lag")
        except RedisError as error:
            log.warning("Unable to inspect telemetry writer group: %s", error)
    alert_cnt  = await alerts.count_documents(query)
    last       = await col.find_one(query, sort=[("ts", -1)])
    return {
        "total_events":   total,
        "stream_length":  stream_len,
        "stream_pending": stream_pending,
        "stream_lag":     stream_lag,
        "writer_group_found": writer_group_found,
        "alert_count":    alert_cnt,
        "last_event_ts":  last["ts"] if last else None,
        "session": {
            "room_name":  session["room_name"] if session else None,
            "started_at": session["started_at"] if session else None,
        } if session else None,
    }


# ── Telemetry ──────────────────────────────────────────────────────────────────

@app.get("/telemetry/latest")
async def latest(room: str | None = None, limit: int = 20, role: str | None = None):
    query = {"room_name": room} if room else {}
    if role == "trainee":
        query["$or"] = [{"telemetry.role": "trainee"}, {"telemetry.role": None}]
    elif role:
        query["telemetry.role"] = role
    return await col.find(query, {"_id": 0},
                          sort=[("ts", -1)], limit=limit).to_list(limit)


@app.get("/telemetry/history")
async def telemetry_history(
    room: str,
    participant: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
):
    query = {
        "room_name": room,
        "$or": [{"telemetry.role": "trainee"}, {"telemetry.role": None}],
    }
    if participant:
        query["participant"] = participant

    total = await col.count_documents(query)
    participants = await col.distinct("participant", {
        "room_name": room,
        "$or": [{"telemetry.role": "trainee"}, {"telemetry.role": None}],
    })
    docs = await col.find(
        query,
        {"_id": 0, "room_name": 1, "participant": 1, "server_ts": 1,
         "ts": 1, "pts_ms": 1, "telemetry": 1, "packet_b64": 1,
         "packet_encoding": 1, "telemetry_decoded": 1},
    ).sort([("server_ts", -1), ("_id_redis", -1)]).skip(
        (page - 1) * page_size
    ).limit(page_size).to_list(page_size)

    return {
        "items": [{
            "received_ms": (
                doc.get("server_ts")
                if doc.get("server_ts") is not None else doc.get("ts")
            ),
            "pts_ms": doc.get("pts_ms"),
            "participant": doc.get("participant", "unknown"),
            "telemetry": doc.get("telemetry", {}),
            "packet_b64": doc.get("packet_b64"),
            "packet_encoding": doc.get("packet_encoding"),
            "telemetry_decoded": doc.get("telemetry_decoded"),
        } for doc in docs],
        "participants": sorted(name for name in participants if name),
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": (total + page_size - 1) // page_size,
    }


@app.get("/telemetry/series")
async def series(room: str = "poc-room", limit: int = 200, role: str | None = None):
    query = {"room_name": room}
    if role == "trainee":
        query["$or"] = [{"telemetry.role": "trainee"}, {"telemetry.role": None}]
    elif role:
        query["telemetry.role"] = role
    docs = await col.find(
        query,
        {"_id": 0, "pts_ms": 1, "participant": 1,
         "telemetry.role": 1, "telemetry.speed": 1,
         "telemetry.heart_rate": 1, "telemetry.cadence": 1,
         "telemetry.gps": 1},
        sort=[("pts_ms", -1 if role else 1)], limit=limit
    ).to_list(limit)
    if role:
        docs.reverse()
    series_data = []
    for doc in docs:
        telemetry = doc.get("telemetry")
        if not isinstance(telemetry, dict):
            telemetry = {}
        gps = telemetry.get("gps")
        if not isinstance(gps, dict):
            gps = {}

        series_data.append({
            "pts_ms":      doc.get("pts_ms"),
            "participant": doc.get("participant", "unknown"),
            "role":        telemetry.get("role", "trainee"),
            "speed":       telemetry.get("speed", 0),
            "heart_rate":  telemetry.get("heart_rate", 0),
            "cadence":     telemetry.get("cadence", 0),
            "lat":         gps.get("lat"),
            "lng":         gps.get("lng"),
            "altitude":    gps.get("altitude", 0),
        })
    return series_data


@app.get("/recordings/{egress_id}/telemetry")
async def recording_telemetry(egress_id: str, limit: int = 100000):
    """Return telemetry from MongoDB on the selected recording's video timeline."""
    rec = await recs.find_one(
        {"egress_id": egress_id},
        {"_id": 0, "room_name": 1, "started_at": 1, "ended_at": 1,
         "video_started_at": 1, "video_ended_at": 1},
    )
    if not rec:
        return []

    start_ms = _epoch_ms(rec.get("video_started_at")) or _epoch_ms(rec.get("started_at"))
    if start_ms is None:
        return []

    server_ts: dict[str, float] = {"$gte": start_ms}
    end_ms = _epoch_ms(rec.get("video_ended_at")) or _epoch_ms(rec.get("ended_at"))
    if end_ms is not None:
        server_ts["$lte"] = end_ms

    docs = await col.find(
        {"room_name": rec["room_name"], "server_ts": server_ts},
        {"_id": 0, "participant": 1, "server_ts": 1, "capture_ts": 1,
         "packet_b64": 1, "telemetry_decoded": 1,
         "pts_ms": 1, "telemetry.role": 1, "telemetry.speed": 1,
         "telemetry.heart_rate": 1, "telemetry.cadence": 1,
         "telemetry.gps": 1, "telemetry.bearing": 1},
        sort=[("server_ts", 1)],
        limit=max(1, min(limit, 250000)),
    ).to_list(max(1, min(limit, 250000)))

    result = []
    for doc in docs:
        telemetry = doc.get("telemetry", {})
        gps = telemetry.get("gps", {})
        received_ms = float(doc.get("server_ts", start_ms))
        binary_packet = bool(doc.get("packet_b64"))
        binary_decoded = binary_packet and bool(doc.get("telemetry_decoded"))
        timeline_ms = (float(doc.get("capture_ts", received_ms))
                       if binary_decoded else received_ms)
        result.append({
            "video_ms": max(0.0, timeline_ms - start_ms),
            "pts_ms": float(doc.get("pts_ms", 0)),
            "participant": doc.get("participant", "unknown"),
            "binary_packet": binary_packet,
            "binary_decoded": binary_decoded,
            "role": telemetry.get("role", "trainee"),
            "speed": None if binary_packet and not binary_decoded else telemetry.get("speed", 0),
            "heart_rate": None if binary_packet and not binary_decoded else telemetry.get("heart_rate", 0),
            "cadence": None if binary_packet and not binary_decoded else telemetry.get("cadence", 0),
            "bearing": None if binary_packet and not binary_decoded else telemetry.get("bearing"),
            "lat": None if binary_packet and not binary_decoded else gps.get("lat", 0),
            "lng": None if binary_packet and not binary_decoded else gps.get("lng", 0),
            "altitude": None if binary_packet and not binary_decoded else gps.get("altitude", 0),
        })
    return result


@app.get("/telemetry/export")
async def export_csv(room: str = "poc-room"):
    """Export CSV de toute la session — téléchargeable."""
    docs = await col.find(
        {"room_name": room}, {"_id": 0},
        sort=[("pts_ms", 1)]
    ).to_list(100_000)

    lines = ["pts_ms,participant,role,speed,heart_rate,cadence,lat,lng,altitude,ts"]
    for d in docs:
        t = d.get("telemetry", {})
        g = t.get("gps", {})
        lines.append(
            f"{d['pts_ms']:.0f},{d.get('participant','')},"
            f"{t.get('role','trainee')},"
            f"{t.get('speed',0)},{t.get('heart_rate',0)},{t.get('cadence',0)},"
            f"{g.get('lat',0)},{g.get('lng',0)},{g.get('altitude',0)},"
            f"{d.get('ts',0):.0f}"
        )
    csv_content = "\n".join(lines)
    return StreamingResponse(
        iter([csv_content]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={room}_telemetry.csv"},
    )


# ── Alertes ────────────────────────────────────────────────────────────────────

@app.get("/alerts")
async def get_alerts(room: str = "poc-room", limit: int = 50, role: str | None = None):
    query = {"room_name": room}
    if role:
        telemetry_query = {"room_name": room}
        if role == "trainee":
            telemetry_query["$or"] = [{"telemetry.role": "trainee"}, {"telemetry.role": None}]
        else:
            telemetry_query["telemetry.role"] = role
        participants = await col.distinct(
            "participant", telemetry_query)
        if not participants:
            return []
        query["participant"] = {"$in": participants}
    docs = await alerts.find(
        query, {"_id": 0},
        sort=[("ts", -1)], limit=limit
    ).to_list(limit)
    return docs


# ─── Rooms / Sessions ────────────────────────────────────────────────────────

@app.get("/mobile/rooms")
async def list_mobile_rooms(
    limit: int = Query(default=50, ge=1, le=100),
    user: dict = Depends(firebase_user),
):
    # POC behavior: any authenticated mobile user can discover every active room.
    query: dict[str, Any] = {"finished": {"$ne": True}}

    projection = {
        "_id": 0,
        "room_name": 1,
        "count": 1,
        "recording": 1,
        "created_at": 1,
        "updated_at": 1,
        "mobile_recording.status": 1,
    }
    room_docs = await rooms.find(query, projection).sort(
        "updated_at", -1
    ).limit(limit).to_list(length=limit)

    result = []
    for room in room_docs:
        for field in ("created_at", "updated_at"):
            if isinstance(room.get(field), datetime):
                room[field] = room[field].isoformat()
        result.append({
            "room_name": room["room_name"],
            "count": room.get("count", 0),
            "recording": room.get("recording", False),
            "recording_status": (room.get("mobile_recording") or {}).get(
                "status", "not_started"
            ),
            "created_at": room.get("created_at"),
            "updated_at": room.get("updated_at"),
            # POC UI behavior: advertise recording controls to every authenticated user.
            "can_control_recording": True,
        })
    return {"rooms": result, "count": len(result)}


@app.get("/rooms")
async def list_rooms(limit: int = 50):
    room_names = set()
    docs_by_room: dict[str, dict] = {}

    async for d in rooms.find({}, {"_id": 0}).sort("updated_at", -1).limit(limit):
        name = d.get("room_name")
        if not name:
            continue
        if isinstance(d.get("updated_at"), datetime):
            d["updated_at"] = d["updated_at"].isoformat()
        docs_by_room[name] = d
        room_names.add(name)

    async for d in sess.find({}, {"_id": 0, "room_name": 1, "started_at": 1}).sort("started_at", -1).limit(limit):
        name = d.get("room_name")
        if not name or name in room_names:
            continue
        room_names.add(name)
        docs_by_room[name] = {
            "room_name": name,
            "participants": [],
            "count": 0,
            "recording": False,
            "finished": False,
            "started_at": d.get("started_at"),
        }

    if not docs_by_room:
        docs_by_room["poc-room"] = {
            "room_name": "poc-room",
            "participants": [],
            "count": 0,
            "recording": False,
            "finished": False,
        }

    return list(docs_by_room.values())


@app.post("/rooms")
async def create_room(body: dict):
    room_name = (body.get("room_name") or body.get("name") or "").strip()
    if not room_name:
        return {"error": "room_name is required"}

    now = datetime.now(UTC)
    await rooms.update_one(
        {"room_name": room_name},
        {"$setOnInsert": {
            "room_name": room_name,
            "participants": [],
            "count": 0,
            "egress_ids": [],
            "recording": False,
            "finished": False,
            "created_at": now,
        }, "$set": {"updated_at": now}},
        upsert=True,
    )
    await sess.update_one(
        {"room_name": room_name},
        {"$setOnInsert": {
            "room_name": room_name,
            "started_at": now.isoformat(),
            "status": "waiting",
        }},
        upsert=True,
    )
    return {"room_name": room_name, "status": "ready"}


@app.get("/rooms/{room_name}")
async def get_room(room_name: str):
    doc = await rooms.find_one({"room_name": room_name}, {"_id": 0})
    if not doc:
        doc = {
            "room_name": room_name,
            "participants": [],
            "count": 0,
            "egress_ids": [],
            "recording": False,
            "finished": False,
        }
    if isinstance(doc.get("updated_at"), datetime):
        doc["updated_at"] = doc["updated_at"].isoformat()
    return doc


@app.post("/rooms/{room_name}/remove-participant")
async def remove_room_participant(room_name: str, body: RemoveParticipantBody):
    identity = body.identity.strip()
    if not identity:
        raise HTTPException(status_code=400, detail="L'identité du participant est requise.")
    if identity.lower() in {"telemetry-agent", "egress", "livekit-egress"}:
        raise HTTPException(status_code=403, detail="Les services internes ne peuvent pas être retirés.")

    from livekit import api as lkapi

    lk = lkapi.LiveKitAPI(LK_URL, LK_KEY, LK_SECRET)
    try:
        participants = await lk.room.list_participants(
            lkapi.ListParticipantsRequest(room=room_name))
        if not any(participant.identity == identity for participant in participants.participants):
            raise HTTPException(status_code=404, detail="Ce participant n'est plus dans la room.")
        await lk.room.remove_participant(
            lkapi.RoomParticipantIdentity(room=room_name, identity=identity))
        return {"room_name": room_name, "identity": identity, "status": "removed"}
    except HTTPException:
        raise
    except Exception as error:
        log.exception("Failed to remove participant %s from room %s", identity, room_name)
        raise HTTPException(
            status_code=502,
            detail="LiveKit n'a pas pu retirer ce participant.",
        ) from error
    finally:
        await lk.aclose()


# ── SSE — événements temps réel ───────────────────────────────────────────────

@app.get("/events")
async def sse_events(request: Request, room: str | None = None):
    """
    Server-Sent Events — pousse les updates en temps réel au dashboard.
    Remplace le polling 1.5s par des événements instantanés.
    Subscribe aux channels Redis: telemetry:latest, alerts, egress:events, room:events
    """
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
                if pubsub is None and now >= next_subscribe:
                    candidate = None
                    try:
                        candidate = r.pubsub()
                        await asyncio.wait_for(
                            candidate.subscribe("alerts", "egress:events", "room:events"),
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
                            ignore_subscribe_messages=True, timeout=0.05)
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

                # Télémétrie latest toutes les 500ms
                tick += 1
                if tick % 10 == 0:
                    query = {"room_name": room} if room else {}
                    last = await col.find_one(query, {"_id": 0}, sort=[("ts", -1)])
                    if last and last.get("ts", 0) != last_ts:
                        last_ts = last.get("ts", 0)
                        t = last.get("telemetry", {})
                        payload = {
                            "room_name":    last.get("room_name", ""),
                            "pts_ms":      last.get("pts_ms", 0),
                            "participant": last.get("participant", ""),
                            "role":        t.get("role", "trainee"),
                            "speed":       t.get("speed", 0),
                            "heart_rate":  t.get("heart_rate", 0),
                            "cadence":     t.get("cadence", 0),
                            "rpm":         t.get("rpm", 0),
                            "gear":        t.get("gear", 0),
                            "brake":       t.get("brake", 0),
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


# ── Token ──────────────────────────────────────────────────────────────────────

@app.post("/token")
async def generate_token(body: dict, user: dict = Depends(firebase_user)):
    from livekit import api as lkapi
    room = body.get("room")
    if not isinstance(room, str) or not room.strip() or len(room) > 128:
        raise HTTPException(status_code=422, detail="A valid room is required.")
    room = room.strip()
    room_doc = await rooms.find_one({"room_name": room}, {"_id": 0})
    if not room_doc or room_doc.get("finished"):
        raise HTTPException(status_code=404, detail="Session not found.")

    # POC behavior: any authenticated Firebase user can join an existing active room.
    uid = user["uid"]

    identity = uid
    try:
        token = (
            lkapi.AccessToken(LK_KEY, LK_SECRET)
            .with_identity(identity).with_name(identity)
            .with_ttl(timedelta(minutes=10))
            .with_grants(lkapi.VideoGrants(
                room_join=True, room=room,
                can_publish=True, can_subscribe=True))
            .to_jwt()
        )
        return {"token": token, "room": room, "identity": identity, "url": LK_PUB_URL}
    except Exception as error:
        log.exception("Failed to issue LiveKit token for Firebase user %s", uid)
        raise HTTPException(status_code=502, detail="Unable to issue a LiveKit token.") from error


# ── Recordings ─────────────────────────────────────────────────────────────────

@app.post("/mobile/rooms/{room_name}/recording/start")
async def mobile_start_recording(
    room_name: str,
    layout: Literal["grid", "speaker", "single-speaker"] = "grid",
    user: dict = Depends(firebase_user),
):
    from livekit import api as lkapi
    from livekit.protocol import egress as proto_egress

    _require_manual_recording_mode()
    await _get_authorized_room(room_name, user)

    active = await recs.find_one({
        "room_name": room_name,
        "status": {"$in": [
            "recording", "EGRESS_STARTING", "EGRESS_ACTIVE", "EGRESS_ENDING",
        ]},
    }, {"_id": 0, "egress_id": 1})
    if active:
        raise HTTPException(
            status_code=409,
            detail="An Egress recording is already active for this session.",
        )

    request_id = uuid.uuid4().hex
    requested_at = datetime.now(UTC)
    reservation = await rooms.update_one(
        {
            "room_name": room_name,
            "finished": {"$ne": True},
            "$or": [
                {"mobile_recording.status": {"$in": [
                    "completed", "failed", "stopped",
                    "EGRESS_COMPLETE", "EGRESS_FAILED", "EGRESS_ABORTED",
                ]}},
                {"mobile_recording": {"$exists": False}},
            ],
        },
        {"$set": {"mobile_recording": {
            "request_id": request_id,
            "status": "starting",
            "requested_by": user["uid"],
            "requested_at": requested_at,
        }}},
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
        request = proto_egress.RoomCompositeEgressRequest(
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
                width=1280, height=720, depth=24, framerate=24,
                audio_codec=2, audio_bitrate=128, audio_frequency=44100,
                video_codec=1, video_bitrate=2500, key_frame_interval=4,
            ),
        )
        info = await lk.egress.start_room_composite_egress(request)
        egress_id = info.egress_id
        status = _egress_status(info.status)
        actual_started_at = datetime.now(UTC)
        stop_deadline_at = actual_started_at + timedelta(
            seconds=RECORDING_MAX_DURATION_SECONDS
        )
        await recs.update_one(
            {"egress_id": info.egress_id},
            {"$set": {
                "room_name": room_name,
                "requested_by": user["uid"],
                "source": "mobile",
                "requested_at": requested_at.isoformat(),
                "stop_deadline_at": stop_deadline_at,
                "max_duration_seconds": RECORDING_MAX_DURATION_SECONDS,
            }, "$setOnInsert": {
                "egress_id": info.egress_id,
                "status": status,
                "files": [],
            }},
            upsert=True,
        )
        await rooms.update_one(
            {"room_name": room_name, "mobile_recording.request_id": request_id},
            {"$set": {
                "mobile_recording.egress_id": info.egress_id,
                "mobile_recording.status": status,
                "mobile_recording.stop_deadline_at": stop_deadline_at,
            }},
        )
        latest = await recs.find_one(
            {"egress_id": info.egress_id}, {"_id": 0, "status": 1}
        )
        if latest and latest.get("status"):
            await rooms.update_one(
                {"room_name": room_name, "mobile_recording.request_id": request_id},
                {"$set": {"mobile_recording.status": latest["status"]}},
            )
        return {
            "room_name": room_name,
            "egress_id": info.egress_id,
            "status": status,
            "max_duration_seconds": RECORDING_MAX_DURATION_SECONDS,
            "stop_deadline_at": stop_deadline_at,
        }
    except Exception as error:
        if egress_id:
            try:
                await lk.egress.stop_egress(
                    proto_egress.StopEgressRequest(egress_id=egress_id)
                )
            except Exception:
                log.exception("Failed to roll back Egress %s after persistence error", egress_id)
        await rooms.update_one(
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


@app.post("/mobile/rooms/{room_name}/recording/stop")
async def mobile_stop_recording(room_name: str, user: dict = Depends(firebase_user)):
    from livekit import api as lkapi
    from livekit.protocol import egress as proto_egress

    _require_manual_recording_mode()
    room = await _get_authorized_room(room_name, user)
    control = room.get("mobile_recording") or {}
    egress_id = control.get("egress_id")
    if not egress_id:
        raise HTTPException(status_code=409, detail="No mobile recording is active.")

    recording = await recs.find_one(
        {"egress_id": egress_id, "room_name": room_name},
        {"_id": 0, "status": 1},
    )
    if not recording or recording.get("status") in {
        "completed", "failed", "stopped", "EGRESS_COMPLETE", "EGRESS_FAILED",
        "EGRESS_ABORTED",
    }:
        raise HTTPException(status_code=409, detail="No active recording was found.")

    previous_status = control.get("status", "recording")
    stop_reservation = await rooms.update_one(
        {
            "room_name": room_name,
            "mobile_recording.egress_id": egress_id,
            "mobile_recording.status": {"$nin": [
                "stopping", "completed", "failed", "stopped", "EGRESS_ENDING",
                "EGRESS_COMPLETE", "EGRESS_FAILED", "EGRESS_ABORTED",
            ]},
        },
        {"$set": {"mobile_recording.status": "stopping"}},
    )
    if stop_reservation.modified_count != 1:
        raise HTTPException(status_code=409, detail="The recording is already stopping.")

    lk = None
    stop_requested_at = datetime.now(UTC)
    try:
        await recs.update_one(
            {"egress_id": egress_id},
            {"$set": {
                "mobile_stop_requested_at": stop_requested_at,
                "stop_reason": "mobile",
            }},
        )
        lk = lkapi.LiveKitAPI(LK_URL, LK_KEY, LK_SECRET)
        info = await lk.egress.stop_egress(
            proto_egress.StopEgressRequest(egress_id=egress_id)
        )
        await rooms.update_one(
            {"room_name": room_name, "mobile_recording.egress_id": egress_id},
            {"$set": {
                "mobile_recording.stopped_by": user["uid"],
                "mobile_recording.stop_requested_at": datetime.now(UTC),
            }},
        )
        return {
            "room_name": room_name,
            "egress_id": info.egress_id,
            "status": _egress_status(info.status),
        }
    except Exception as error:
        await recs.update_one(
            {"egress_id": egress_id},
            {"$unset": {"mobile_stop_requested_at": "", "stop_reason": ""}},
        )
        await rooms.update_one(
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


@app.get("/mobile/rooms/{room_name}/recording")
async def mobile_recording_status(room_name: str, user: dict = Depends(firebase_user)):
    room = await _get_authorized_room(room_name, user, allow_finished=True)
    control = room.get("mobile_recording") or {}
    egress_id = control.get("egress_id")
    if not egress_id:
        return {"room_name": room_name, "status": control.get("status", "not_started")}

    recording = await recs.find_one({"egress_id": egress_id}, {"_id": 0})
    if not recording:
        return {
            "room_name": room_name,
            "egress_id": egress_id,
            "status": control.get("status", "starting"),
        }
    _sign_recording_files(recording)
    return recording


@app.get("/recordings")
async def list_recordings(room: str | None = None, room_name: str | None = None, limit: int = 50):
    selected_room = room_name or room
    query = {"room_name": selected_room} if selected_room else {}
    docs = await recs.find(query, {"_id": 0}, sort=[("started_at", -1)]).to_list(limit)
    for d in docs:
        for f in ("started_at", "ended_at"):
            if isinstance(d.get(f), datetime): d[f] = d[f].isoformat()
        _sign_recording_files(d)
    return docs


@app.get("/recordings/files")
async def list_files(room: str | None = None, room_name: str | None = None):
    from minio import Minio
    try:
        internal_mc = _minio_client()
        public_mc = _minio_client(public=True)
        selected_room = room_name or room
        prefix = f"{selected_room}/" if selected_room else None
        return [{"key": o.object_name, "size_bytes": o.size,
                 "size_mb": round((o.size or 0)/(1024*1024), 2),
                 "last_modified": o.last_modified.isoformat() if o.last_modified else None,
                 "url": public_mc.presigned_get_object(REC_BUCKET, o.object_name,
                                                        expires=timedelta(hours=1))}
                for o in internal_mc.list_objects(REC_BUCKET, prefix=prefix, recursive=True)]
    except Exception as e:
        return {"error": str(e)}


@app.get("/recordings/{egress_id}")
async def get_recording(egress_id: str):
    doc = await recs.find_one({"egress_id": egress_id}, {"_id": 0})
    if doc:
        _sign_recording_files(doc)
    return doc or {}


@app.post("/recordings/start")
async def start_recording(room_name: str, layout: str = "grid"):
    from livekit import api as lkapi
    from livekit.protocol import egress as proto_egress
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    lk = lkapi.LiveKitAPI(LK_URL, LK_KEY, LK_SECRET)
    try:
        req = proto_egress.RoomCompositeEgressRequest(
            room_name=room_name, layout=layout,
            audio_only=False, video_only=False,
            file=proto_egress.EncodedFileOutput(
                file_type=1,
                filepath=f"{room_name}/{ts}/recording",
                s3=proto_egress.S3Upload(
                    access_key=MINIO_AK, secret=MINIO_SK,
                    bucket=REC_BUCKET, endpoint=MINIO_EP, force_path_style=True,
                ),
            ),
            advanced=proto_egress.EncodingOptions(
                width=1280, height=720, depth=24, framerate=24,
                audio_codec=2, audio_bitrate=128, audio_frequency=44100,
                video_codec=1, video_bitrate=2500, key_frame_interval=4,
            ),
        )
        info = await lk.egress.start_room_composite_egress(req)
        return {"egress_id": info.egress_id, "room_name": info.room_name,
                "status": _egress_status(info.status),
                "output": f"s3://{REC_BUCKET}/{room_name}/{ts}/"}
    except Exception as e:
        err = str(e)
        if "unavailable" in err.lower() or "no response from servers" in err.lower():
            return {
                "error": err,
                "cause": "Le service Egress n'est pas disponible.",
                "solutions": [
                    "1. Vérifier que le container vl-egress est démarré: docker ps | grep egress",
                    "2. Vérifier les logs Egress: docker logs vl-egress",
                    "3. Vérifier que Redis fonctionne: docker exec vl-redis redis-cli ping",
                    "4. En cas de corruption Redis: docker compose down && docker volume rm videolog-poc_redis_data && docker compose up --build",
                    "5. Diagnostique: GET http://localhost:8001/egress/status"
                ]
            }
        return {"error": err}
    finally:
        await lk.aclose()


@app.post("/recordings/stop")
async def stop_recording(egress_id: str):
    from livekit import api as lkapi
    from livekit.protocol import egress as proto_egress
    lk = lkapi.LiveKitAPI(LK_URL, LK_KEY, LK_SECRET)
    try:
        info = await lk.egress.stop_egress(
            proto_egress.StopEgressRequest(egress_id=egress_id))
        return {"egress_id": info.egress_id, "room_name": info.room_name,
                "status": _egress_status(info.status),
                "files": [{"filename": f.filename, "size_bytes": f.size,
                            "duration_s": f.duration,
                            "minio_url": f"{MINIO_PUBLIC_URL}/{REC_BUCKET}/{f.filename}"}
                           for f in (info.file_results or [])]}
    except Exception as e:
        return {"error": str(e)}
    finally:
        await lk.aclose()


# ── Webhook ────────────────────────────────────────────────────────────────────

@app.post("/webhook/livekit")
async def livekit_webhook(request: Request):
    from livekit import api as lkapi

    raw_body = await request.body()
    auth_header = request.headers.get("authorization")
    if not auth_header:
        raise HTTPException(status_code=401, detail="Missing LiveKit webhook authorization.")
    try:
        receiver = lkapi.WebhookReceiver(LK_KEY, LK_SECRET)
        await asyncio.to_thread(receiver.receive, raw_body, auth_header)
        body = json.loads(raw_body)
    except Exception as error:
        log.warning("Rejected invalid LiveKit webhook: %s", error)
        raise HTTPException(status_code=401, detail="Invalid LiveKit webhook signature.") from error

    event       = body.get("event", "")
    egress_info = body.get("egressInfo", {})
    if not egress_info: return {"ok": True}
    egress_id = egress_info.get("egressId", "")
    room_name = egress_info.get("roomName", "")
    if event == "egress_started":
        await recs.update_one({"egress_id": egress_id}, {"$set": {
            "egress_id": egress_id, "room_name": room_name, "status": "recording",
            "started_at": _livekit_timestamp_iso(egress_info.get("startedAt")), "files": [],
        }}, upsert=True)
        await rooms.update_one(
            {"room_name": room_name, "mobile_recording.egress_id": egress_id},
            {"$set": {"mobile_recording.status": "recording"}},
        )
        if r: await r.publish("egress:events", json.dumps(
            {"event": "started", "egress_id": egress_id, "room": room_name}))
    elif event == "egress_ended":
        file_results = egress_info.get("fileResults") or []
        files = [{"filename": f.get("filename",""), "size": f.get("size",0),
                  "duration": f.get("duration",0),
                  "url": f"{MINIO_PUBLIC_URL}/{REC_BUCKET}/{f.get('filename','').lstrip('/')}"}
                 for f in file_results]
        error = egress_info.get("error","")
        update = {
            "status": "failed" if error else "completed",
            "ended_at": _livekit_timestamp_iso(egress_info.get("endedAt")),
            "files": files, "error": error or None,
        }
        if file_results:
            update["video_started_at"] = _livekit_timestamp_iso(file_results[0].get("startedAt"))
            update["video_ended_at"] = _livekit_timestamp_iso(file_results[0].get("endedAt"))
        await recs.update_one({"egress_id": egress_id}, {"$set": update}, upsert=True)
        await rooms.update_one(
            {"room_name": room_name, "mobile_recording.egress_id": egress_id},
            {"$set": {"mobile_recording.status": update["status"]}},
        )
        if r: await r.publish("egress:events", json.dumps(
            {"event": "ended", "egress_id": egress_id,
             "status": "failed" if error else "completed", "files": files}))
    elif event == "egress_updated":
        status = egress_info.get("status", "")
        await recs.update_one({"egress_id": egress_id},
            {"$set": {"status": status,
                      "updated_at": datetime.now(UTC).isoformat()}}, upsert=True)
        await rooms.update_one(
            {"room_name": room_name, "mobile_recording.egress_id": egress_id},
            {"$set": {"mobile_recording.status": status}},
        )
    return {"ok": True}


def _egress_status(code) -> str:
    try:
        from livekit.protocol import egress as proto_egress
        return proto_egress.EgressStatus.Name(code)
    except Exception:
        return str(code)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="info")
