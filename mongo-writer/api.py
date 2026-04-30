"""
Dashboard API — Videolog POC
FastAPI avec Server-Sent Events (SSE) pour le dashboard temps réel.
"""
import asyncio, json, os, time
from datetime import datetime, UTC
from typing import AsyncGenerator, Any

import redis.asyncio as aioredis
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from motor.motor_asyncio import AsyncIOMotorClient

MONGO_URL  = os.environ.get("MONGO_URL",         "mongodb://mongo:27017/videolog")
REDIS_URL  = os.environ.get("REDIS_URL",         "redis://redis:6379")
LK_URL     = os.environ.get("LIVEKIT_URL",       "http://livekit:7880")
LK_KEY     = os.environ.get("LIVEKIT_API_KEY",   "devkey")
LK_SECRET  = os.environ.get("LIVEKIT_API_SECRET","secret")
LK_PUB_URL = os.environ.get("LIVEKIT_PUBLIC_URL","ws://10.0.2.2:7880")
MINIO_EP   = os.environ.get("MINIO_ENDPOINT",    "http://minio:9000")
MINIO_AK   = os.environ.get("MINIO_ACCESS_KEY",  "videolog")
MINIO_SK   = os.environ.get("MINIO_SECRET_KEY",  "videolog123")
REC_BUCKET = "recordings"
MINIO_PUBLIC_URL = os.environ.get("MINIO_PUBLIC_URL", "http://localhost:9000")

app = FastAPI(title="Videolog Dashboard API")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

mongo  = AsyncIOMotorClient(MONGO_URL)
col    = mongo.videolog.telemetry
recs   = mongo.videolog.recordings
sess   = mongo.videolog.sessions
alerts = mongo.videolog.alerts
r: aioredis.Redis = None


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
async def stats():
    total      = await col.count_documents({})
    session    = await sess.find_one({}, sort=[("started_at", -1)])
    stream_len = await r.xlen("telemetry:stream") if r else 0
    alert_cnt  = await alerts.count_documents({})
    last       = await col.find_one({}, sort=[("ts", -1)])
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
async def latest(limit: int = 20):
    return await col.find({}, {"_id": 0},
                          sort=[("ts", -1)], limit=limit).to_list(limit)


@app.get("/telemetry/series")
async def series(room: str = "poc-room", limit: int = 200):
    docs = await col.find(
        {"room_name": room},
        {"_id": 0, "pts_ms": 1, "participant": 1,
         "telemetry.role": 1, "telemetry.speed": 1,
         "telemetry.heart_rate": 1, "telemetry.cadence": 1,
         "telemetry.gps": 1},
        sort=[("pts_ms", 1)], limit=limit
    ).to_list(limit)
    return [{
        "pts_ms":      d["pts_ms"],
        "participant": d.get("participant", "unknown"),
        "role":        d["telemetry"].get("role", "trainee"),
        "speed":       d["telemetry"].get("speed", 0),
        "heart_rate":  d["telemetry"].get("heart_rate", 0),
        "cadence":     d["telemetry"].get("cadence", 0),
        "lat":         d["telemetry"]["gps"]["lat"],
        "lng":         d["telemetry"]["gps"]["lng"],
        "altitude":    d["telemetry"]["gps"].get("altitude", 0),
    } for d in docs]


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
async def get_alerts(room: str = "poc-room", limit: int = 50):
    docs = await alerts.find(
        {"room_name": room}, {"_id": 0},
        sort=[("ts", -1)], limit=limit
    ).to_list(limit)
    return docs


# ── SSE — événements temps réel ───────────────────────────────────────────────

@app.get("/events")
async def sse_events(request: Request):
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

                    if channel == "alerts":
                        yield f"event: alert\ndata: {json.dumps(data)}\n\n"
                    elif channel == "egress:events":
                        yield f"event: egress\ndata: {json.dumps(data)}\n\n"
                    elif channel == "room:events":
                        yield f"event: room\ndata: {json.dumps(data)}\n\n"

                # Télémétrie latest toutes les 500ms
                tick += 1
                if tick % 10 == 0:
                    last = await col.find_one({}, {"_id": 0}, sort=[("ts", -1)])
                    if last and last.get("ts", 0) != last_ts:
                        last_ts = last.get("ts", 0)
                        t = last.get("telemetry", {})
                        payload = {
                            "pts_ms":      last.get("pts_ms", 0),
                            "participant": last.get("participant", ""),
                            "speed":       t.get("speed", 0),
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
async def list_recordings():
    docs = await recs.find({}, {"_id": 0}, sort=[("started_at", -1)]).to_list(50)
    for d in docs:
        for f in ("started_at", "ended_at"):
            if isinstance(d.get(f), datetime): d[f] = d[f].isoformat()
    return docs


@app.get("/recordings/files")
async def list_files():
    from minio import Minio
    try:
        mc = Minio(MINIO_EP.replace("http://","").replace("https://",""),
                   access_key=MINIO_AK, secret_key=MINIO_SK, secure=False)
        return [{"key": o.object_name, "size_bytes": o.size,
                 "size_mb": round((o.size or 0)/(1024*1024), 2),
                 "last_modified": o.last_modified.isoformat() if o.last_modified else None,
                 "url": f"{MINIO_PUBLIC_URL}/{REC_BUCKET}/{o.object_name}"}
                for o in mc.list_objects(REC_BUCKET, recursive=True)]
    except Exception as e:
        return {"error": str(e)}


@app.get("/recordings/{egress_id}")
async def get_recording(egress_id: str):
    doc = await recs.find_one({"egress_id": egress_id}, {"_id": 0})
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
            "started_at": datetime.now(UTC).isoformat(), "files": [],
        }}, upsert=True)
        if r: await r.publish("egress:events", json.dumps(
            {"event": "started", "egress_id": egress_id, "room": room_name}))
    elif event == "egress_ended":
        files = [{"filename": f.get("filename",""), "size": f.get("size",0),
                  "duration": f.get("duration",0),
                  "url": f"{MINIO_PUBLIC_URL}/{REC_BUCKET}/{f.get('filename','').lstrip('/')}"}
                 for f in (egress_info.get("fileResults") or [])]
        error = egress_info.get("error","")
        await recs.update_one({"egress_id": egress_id}, {"$set": {
            "status": "failed" if error else "completed",
            "ended_at": datetime.now(UTC).isoformat(), "files": files, "error": error or None,
        }}, upsert=True)
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
