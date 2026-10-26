"""
Egress Webhook — Videolog Sport POC

Regles d'enregistrement automatique:
  - START : des que RECORD_MIN_PARTICIPANTS (defaut: 2) sont dans la room
  - STOP  : quand il reste < RECORD_MIN_PARTICIPANTS (avec delai de grace)

Agents internes exclus du comptage: telemetry-agent, egress
"""
import asyncio, json, logging, os, time
from datetime import datetime, UTC

from fastapi import FastAPI, Request, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
import redis.asyncio as aioredis
from livekit import api as lkapi
from livekit.protocol import egress as proto_egress

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [WEBHOOK] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

MONGO_URL    = os.environ.get("MONGO_URL",          "mongodb://mongo:27017/videolog")
REDIS_URL    = os.environ.get("REDIS_URL",          "redis://redis:6379")
API_KEY      = os.environ["LIVEKIT_API_KEY"]
API_SECRET   = os.environ["LIVEKIT_API_SECRET"]
LIVEKIT_URL  = os.environ.get("LIVEKIT_URL",        "http://livekit:7880")
MINIO_EP     = os.environ.get("MINIO_ENDPOINT",     "http://minio:9000")
MINIO_AK     = os.environ["MINIO_ACCESS_KEY"]
MINIO_SK     = os.environ["MINIO_SECRET_KEY"]
REC_BUCKET      = "recordings"
MINIO_PUBLIC_URL = os.environ["MINIO_PUBLIC_URL"]

# Nombre minimum de participants pour declencher l'enregistrement
RECORD_MIN_PARTICIPANTS = int(os.environ.get("RECORD_MIN_PARTICIPANTS", "2"))
# Delai de grace avant arret (secondes)
STOP_GRACE_DELAY        = int(os.environ.get("STOP_GRACE_DELAY", "10"))
# Participants internes a exclure du comptage
INTERNAL_IDENTITIES     = {"telemetry-agent", "egress", "livekit-egress"}

app = FastAPI(title="Egress Webhook")
mongo     = AsyncIOMotorClient(MONGO_URL)
col       = mongo.videolog.recordings
rooms_col = mongo.videolog.room_state
r: aioredis.Redis = None

# { room_name -> {"participants": set(), "egress_ids": set(), "stop_task": Task} }
_state: dict = {}


@app.on_event("startup")
async def startup():
    global r
    r = aioredis.from_url(REDIS_URL, decode_responses=True)
    log.info(f"✅ Webhook pret | min_participants={RECORD_MIN_PARTICIPANTS} "
             f"grace={STOP_GRACE_DELAY}s")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _room(name: str) -> dict:
    if name not in _state:
        _state[name] = {"participants": set(), "egress_ids": set(), "stop_task": None}
    return _state[name]


def _count(room_name: str) -> int:
    """Nombre de participants reels (hors agents internes)."""
    st = _state.get(room_name)
    return sum(1 for p in st["participants"] if p not in INTERNAL_IDENTITIES) if st else 0


def _status(code) -> str:
    try:    return proto_egress.EgressStatus.Name(code)
    except: return str(code)


def _livekit_timestamp_iso(value) -> str | None:
    try:
        timestamp_ns = int(value)
        if timestamp_ns <= 0:
            raise ValueError("LiveKit timestamp is missing")
        return datetime.fromtimestamp(timestamp_ns / 1_000_000_000, UTC).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


async def _cancel_stop(room_name: str):
    st   = _room(room_name)
    task = st.get("stop_task")
    if task and not task.done():
        task.cancel()
        st["stop_task"] = None
        log.info(f"⏪ Stop annule pour '{room_name}'")


async def _auto_start_recording(room_name: str):
    """
    Lance un enregistrement automatiquement.
    Appele des que le seuil RECORD_MIN_PARTICIPANTS est atteint.
    Ne lance pas si un egress est deja actif.
    """
    st = _room(room_name)
    if st["egress_ids"]:
        log.info(f"ℹ️  Enregistrement deja actif pour '{room_name}'")
        return

    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    lk = lkapi.LiveKitAPI(LIVEKIT_URL, API_KEY, API_SECRET)
    try:
        req = proto_egress.RoomCompositeEgressRequest(
            room_name=room_name,
            layout="grid",
            audio_only=False,
            video_only=False,
            file=proto_egress.EncodedFileOutput(
                file_type=1,   # MP4
                filepath=f"{room_name}/{ts}/session",
                s3=proto_egress.S3Upload(
                    access_key=MINIO_AK,
                    secret=MINIO_SK,
                    bucket=REC_BUCKET,
                    endpoint=MINIO_EP,
                    region="us-east-1",              # ✅ AJOUT CRITIQUE
                    force_path_style=True,
                ),
            ),
            advanced=proto_egress.EncodingOptions(
                width=1280, height=720, depth=24, framerate=24,
                audio_codec=2, audio_bitrate=128, audio_frequency=44100,
                video_codec=1, video_bitrate=2500, key_frame_interval=4,
            ),
        )
        info = await lk.egress.start_room_composite_egress(req)
        st["egress_ids"].add(info.egress_id)
        log.info(f"🔴 Enregistrement AUTO demarre | {info.egress_id} "
                 f"room={room_name} ({_count(room_name)} participants)")

        # Persister dans MongoDB
        await col.update_one(
            {"egress_id": info.egress_id},
            {"$set": {
                "egress_id":    info.egress_id,
                "room_name":    room_name,
                "status":       "recording",
                "auto_started": True,
                "started_at":   datetime.now(UTC).isoformat(),
                "files":        [],
            }},
            upsert=True,
        )

        # Notifier dashboard
        await r.publish("egress:events", json.dumps({
            "type": "started", "egress_id": info.egress_id,
            "room_name": room_name, "auto": True,
        }))

    except Exception as e:
        log.error(f"❌ Auto-start echoue pour '{room_name}': {e}")
    finally:
        await lk.aclose()


async def _stop_egress(room_name: str, egress_id: str, reason: str = "manual"):
    lk = lkapi.LiveKitAPI(LIVEKIT_URL, API_KEY, API_SECRET)
    try:
        info = await lk.egress.stop_egress(
            proto_egress.StopEgressRequest(egress_id=egress_id))
        _room(room_name)["egress_ids"].discard(egress_id)
        log.info(f"⏹️  Egress arrete | {egress_id} reason={reason} "
                 f"status={_status(info.status)}")
        await r.publish("egress:events", json.dumps({
            "type": "auto_stopped", "egress_id": egress_id,
            "room_name": room_name, "reason": reason,
        }))
        await col.update_one(
            {"egress_id": egress_id},
            {"$set": {"status": "stopping", "stop_reason": reason,
                      "stopped_at": datetime.now(UTC).isoformat()}},
        )
    except Exception as e:
        log.error(f"❌ Stop egress {egress_id}: {e}")
    finally:
        await lk.aclose()


async def _schedule_stop(room_name: str):
    await _cancel_stop(room_name)
    st = _room(room_name)

    async def _delayed():
        log.info(f"⏳ Grace {STOP_GRACE_DELAY}s pour '{room_name}'...")
        await asyncio.sleep(STOP_GRACE_DELAY)
        if _count(room_name) >= RECORD_MIN_PARTICIPANTS:
            log.info(f"✋ Stop annule — assez de participants revenus")
            return
        for eid in list(st["egress_ids"]):
            await _stop_egress(room_name, eid, reason="participants_below_threshold")

    st["stop_task"] = asyncio.create_task(_delayed())


async def _persist_room(room_name: str, finished: bool = False):
    st = _room(room_name)
    await rooms_col.update_one(
        {"room_name": room_name},
        {"$set": {
            "room_name":    room_name,
            "participants": list(st["participants"]),
            "count":        _count(room_name),
            "egress_ids":   list(st["egress_ids"]),
            "recording":    bool(st["egress_ids"]),
            "finished":     finished,
            "updated_at":   datetime.now(UTC),
        }},
        upsert=True,
    )


# ── Webhook endpoint ───────────────────────────────────────────────────────────

@app.post("/webhook/livekit")
async def livekit_webhook(request: Request):
    body = await request.body()
    try:
        event = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event_type = event.get("event", "")
    log.info(f"📨 {event_type}")

    handlers = {
        "participant_joined": handle_joined,
        "participant_left":   handle_left,
        "egress_started":     handle_egress_started,
        "egress_updated":     handle_egress_updated,
        "egress_ended":       handle_egress_ended,
        "room_started":       handle_room_started,
        "room_finished":      handle_room_finished,
    }
    handler = handlers.get(event_type)
    if handler:
        asyncio.create_task(handler(event))

    return {"ok": True, "event": event_type}


# ── Handlers participants ──────────────────────────────────────────────────────

async def handle_joined(event: dict):
    p         = event.get("participant", {})
    room      = event.get("room", {})
    identity  = p.get("identity", "unknown")
    room_name = room.get("name", "")

    _room(room_name)["participants"].add(identity)
    count = _count(room_name)
    log.info(f"👤 Rejoint | {identity} | {count} participant(s) reel(s) dans '{room_name}'")

    # Annuler un stop en attente si quelqu'un revient
    await _cancel_stop(room_name)
    await _persist_room(room_name)

    await r.publish("room:events", json.dumps({
        "type": "participant_joined", "room_name": room_name,
        "identity": identity, "count": count,
    }))

    # ── Declenchement automatique de l'enregistrement ──────────────────────
    if count >= RECORD_MIN_PARTICIPANTS:
        log.info(f"🎯 Seuil atteint ({count}/{RECORD_MIN_PARTICIPANTS}) "
                 f"→ demarrage enregistrement")
        asyncio.create_task(_auto_start_recording(room_name))


async def handle_left(event: dict):
    p         = event.get("participant", {})
    room      = event.get("room", {})
    identity  = p.get("identity", "unknown")
    room_name = room.get("name", "")

    _room(room_name)["participants"].discard(identity)
    count = _count(room_name)
    log.info(f"👤 Parti | {identity} | {count} participant(s) reel(s) dans '{room_name}'")

    await _persist_room(room_name)
    await r.publish("room:events", json.dumps({
        "type": "participant_left", "room_name": room_name,
        "identity": identity, "count": count,
    }))

    st = _room(room_name)
    if count < RECORD_MIN_PARTICIPANTS and st["egress_ids"]:
        log.info(f"📉 Moins de {RECORD_MIN_PARTICIPANTS} participants "
                 f"→ arret dans {STOP_GRACE_DELAY}s")
        await _schedule_stop(room_name)


# ── Handlers egress ────────────────────────────────────────────────────────────

async def handle_egress_started(event: dict):
    eg        = event.get("egressInfo", {})
    egress_id = eg.get("egressId", "")
    room_name = eg.get("roomName", "")
    _room(room_name)["egress_ids"].add(egress_id)
    await col.update_one({"egress_id": egress_id}, {"$set": {
        "egress_id": egress_id, "room_name": room_name,
        "status": "recording",
        "started_at": _livekit_timestamp_iso(eg.get("startedAt")), "files": [],
    }}, upsert=True)
    await r.publish("egress:events", json.dumps(
        {"type": "started", "egress_id": egress_id, "room_name": room_name}))
    log.info(f"▶  Egress demarre | {egress_id}")


async def handle_egress_updated(event: dict):
    eg = event.get("egressInfo", {})
    await col.update_one({"egress_id": eg.get("egressId","")},
        {"$set": {"status": eg.get("status",""),
                  "updated_at": datetime.now(UTC).isoformat()}})


async def handle_egress_ended(event: dict):
    eg        = event.get("egressInfo", {})
    egress_id = eg.get("egressId", "")
    room_name = eg.get("roomName", "")
    error     = eg.get("error", None)

    _room(room_name)["egress_ids"].discard(egress_id)

    file_results = eg.get("fileResults", [])
    files = [{
        "filename":   f.get("filename", ""),
        "size_bytes": f.get("size", 0),
        "size_mb":    round(f.get("size", 0) / (1024*1024), 2),
        "duration_s": f.get("duration", 0),
        "minio_url":  f"{MINIO_PUBLIC_URL}/{REC_BUCKET}/{f.get('filename','')}",
    } for f in file_results]

    duration_s = max((f["duration_s"] for f in files), default=None)

    update = {
        "status":     "failed" if error else "completed",
        "ended_at":   _livekit_timestamp_iso(eg.get("endedAt")),
        "duration_s": duration_s,
        "files":      files,
        "error":      error,
    }
    if file_results:
        update["video_started_at"] = _livekit_timestamp_iso(file_results[0].get("startedAt"))
        update["video_ended_at"] = _livekit_timestamp_iso(file_results[0].get("endedAt"))
    await col.update_one({"egress_id": egress_id}, {"$set": update}, upsert=True)

    if files and not error:
        await mongo.videolog.sessions.update_one(
            {"room_name": room_name},
            {"$push": {"recordings": {
                "egress_id": egress_id, "files": files,
                "recorded_at": datetime.now(UTC),
            }}},
        )

    await r.publish("egress:events", json.dumps({
        "type": "ended", "egress_id": egress_id,
        "room_name": room_name, "duration_s": duration_s, "error": error,
    }))

    if error: log.error(f"❌ Egress echoue | {egress_id}: {error}")
    else:     log.info(f"✅ Egress termine | {egress_id} {duration_s}s")


# ── Handlers room ──────────────────────────────────────────────────────────────

async def handle_room_started(event: dict):
    name = event.get("room", {}).get("name", "")
    log.info(f"🏠 Room ouverte: {name}")
    _room(name)["participants"].clear()
    await _persist_room(name)


async def handle_room_finished(event: dict):
    name = event.get("room", {}).get("name", "")
    log.info(f"🏠 Room fermee: {name}")
    st = _room(name)
    await _cancel_stop(name)
    for eid in list(st["egress_ids"]):
        await _stop_egress(name, eid, reason="room_finished")
    st["participants"].clear()
    st["egress_ids"].clear()
    await _persist_room(name, finished=True)


# ── REST ───────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "ts": time.time()}


@app.get("/rooms")
async def list_rooms():
    docs = await rooms_col.find(
        {"finished": {"$ne": True}}, {"_id": 0}
    ).to_list(50)
    for d in docs:
        if isinstance(d.get("updated_at"), datetime):
            d["updated_at"] = d["updated_at"].isoformat()
    return docs


@app.get("/rooms/{room_name}")
async def get_room(room_name: str):
    st = _state.get(room_name, {})
    return {
        "room_name":    room_name,
        "participants": list(st.get("participants", set())),
        "count":        _count(room_name),
        "egress_ids":   list(st.get("egress_ids", set())),
        "recording":    bool(st.get("egress_ids")),
        "stop_pending": (st.get("stop_task") is not None
                         and not st["stop_task"].done()
                         if st.get("stop_task") else False),
        "threshold":    RECORD_MIN_PARTICIPANTS,
    }


@app.get("/recordings")
async def get_recordings(room_name: str = None, limit: int = 20):
    query = {}
    if room_name: query["room_name"] = room_name
    docs = await col.find(query, {"_id": 0}).sort("started_at", -1).limit(limit).to_list(limit)
    for d in docs:
        for f in ("started_at", "ended_at", "updated_at", "stopped_at"):
            if isinstance(d.get(f), datetime):
                d[f] = d[f].isoformat()
    return docs


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8088, log_level="info")
