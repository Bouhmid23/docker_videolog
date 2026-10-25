"""
Dashboard API — Videolog POC
FastAPI avec Server-Sent Events (SSE) pour le dashboard temps réel.
"""
import asyncio, json, os, time
from datetime import datetime, UTC, timedelta
from typing import AsyncGenerator, Any

import redis.asyncio as aioredis
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from motor.motor_asyncio import AsyncIOMotorClient

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

app = FastAPI(title="Videolog Dashboard API")
mongo  = AsyncIOMotorClient(MONGO_URL)
col    = mongo.videolog.telemetry
recs   = mongo.videolog.recordings
sess   = mongo.videolog.sessions
alerts = mongo.videolog.alerts
rooms  = mongo.videolog.room_state
r: aioredis.Redis = None


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


# ── Stats ──────────────────────────────────────────────────────────────────────

@app.get("/stats")
async def stats(room: str | None = None):
    query      = {"room_name": room} if room else {}
    total      = await col.count_documents(query)
    session    = await sess.find_one(query, sort=[("started_at", -1)])
    stream_len = await r.xlen("telemetry:stream") if r else 0
    alert_cnt  = await alerts.count_documents(query)
    last       = await col.find_one(query, sort=[("ts", -1)])
    return {
        "total_events":   total,
        "stream_pending": stream_len,
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


# ── SSE — événements temps réel ───────────────────────────────────────────────

@app.get("/events")
async def sse_events(request: Request, room: str | None = None):
    """
    Server-Sent Events — pousse les updates en temps réel au dashboard.
    Remplace le polling 1.5s par des événements instantanés.
    Subscribe aux channels Redis: telemetry:latest, alerts, egress:events, room:events
    """
    async def event_generator() -> AsyncGenerator[str, None]:
        pubsub = r.pubsub()
        await pubsub.subscribe("alerts", "egress:events", "room:events")
        try:
            # Envoyer un ping initial pour établir la connexion
            yield "event: ping\ndata: {}\n\n"

            # Dernier event de télémétrie en polling léger (500ms)
            last_ts = 0.0
            tick = 0

            while True:
                if await request.is_disconnected():
                    break

                # Vérifier les messages Redis pub/sub (non-bloquant)
                msg = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=0.05)
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

                await asyncio.sleep(0.05)

        except asyncio.CancelledError:
            pass
        finally:
            await pubsub.unsubscribe()
            await pubsub.aclose()

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
async def generate_token(body: dict):
    from livekit import api as lkapi
    identity = body.get("identity", "unknown")
    room     = body.get("room", "poc-room")
    try:
        token = (
            lkapi.AccessToken(LK_KEY, LK_SECRET)
            .with_identity(identity).with_name(identity)
            .with_grants(lkapi.VideoGrants(
                room_join=True, room=room,
                can_publish=True, can_subscribe=True))
            .to_jwt()
        )
        return {"token": token, "room": room, "identity": identity, "url": LK_PUB_URL}
    except Exception as e:
        return {"error": str(e)}


# ── Recordings ─────────────────────────────────────────────────────────────────

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
async def start_recording(room_name: str, layout: str = "speaker"):
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
    body        = await request.json()
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
        if r: await r.publish("egress:events", json.dumps(
            {"event": "ended", "egress_id": egress_id,
             "status": "failed" if error else "completed", "files": files}))
    elif event == "egress_updated":
        await recs.update_one({"egress_id": egress_id},
            {"$set": {"status": egress_info.get("status",""),
                      "updated_at": datetime.now(UTC).isoformat()}}, upsert=True)
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
