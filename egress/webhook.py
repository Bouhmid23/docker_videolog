"""
Egress Webhook — Videolog Sport POC

Regles d'enregistrement automatique:
  - START : des que RECORD_MIN_PARTICIPANTS coachs/trainees (defaut: 2)
  - STOP  : quand il reste moins de coachs/trainees (avec delai de grace)

Les observers et agents internes sont exclus du seuil.
Les agents internes sont aussi exclus de l'expulsion pour inactivite.
"""
import asyncio, json, logging, os, time
from datetime import datetime, UTC, timedelta

from fastapi import FastAPI, Request, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
import redis.asyncio as aioredis
from livekit import api as lkapi
from livekit.protocol import room as proto_room
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
RECORD_MIN_PARTICIPANTS = max(2, int(os.environ.get("RECORD_MIN_PARTICIPANTS", "2")))
RECORDING_MODE = os.environ.get("RECORDING_MODE", "manual").strip().lower()
if RECORDING_MODE not in {"automatic", "manual"}:
    raise ValueError("RECORDING_MODE must be 'automatic' or 'manual'.")
RECORDING_MAX_DURATION_SECONDS = max(
    1, int(os.environ.get("RECORDING_MAX_DURATION_SECONDS", "180"))
)
RECORDING_LIMITER_INTERVAL = 1
RECORDING_LIMITER_RETRY_SECONDS = 15
# Delai de grace avant arret (secondes)
STOP_GRACE_DELAY        = int(os.environ.get("STOP_GRACE_DELAY", "10"))
# Identites de service exclues du seuil et de l'expulsion pour inactivite.
INTERNAL_IDENTITIES     = {"telemetry-agent", "egress", "livekit-egress"}
RECORDING_ROLES          = {"coach", "trainee"}
TELEMETRY_IDLE_TIMEOUT  = 60
PARTICIPANT_SWEEP_INTERVAL = 15

app = FastAPI(title="Egress Webhook")
mongo     = AsyncIOMotorClient(MONGO_URL)
col       = mongo.videolog.recordings
telemetry_col = mongo.videolog.telemetry
rooms_col = mongo.videolog.room_state
r: aioredis.Redis = None

# Per-room live participant roles, active Egress IDs, and pending tasks.
_state: dict = {}
_participant_sweeper: asyncio.Task | None = None
_recording_limiter: asyncio.Task | None = None


@app.on_event("startup")
async def startup():
    global r, _participant_sweeper, _recording_limiter
    r = aioredis.from_url(REDIS_URL, decode_responses=True)
    await telemetry_col.create_index(
        [("room_name", 1), ("participant", 1), ("inserted_at", -1),
         ("server_ts", -1)])
    await col.create_index([("stop_deadline_at", 1), ("status", 1)])
    _participant_sweeper = asyncio.create_task(_sweep_inactive_participants())
    _recording_limiter = asyncio.create_task(_enforce_recording_duration_limit())
    log.info(f"✅ Webhook pret | mode={RECORDING_MODE} "
             f"min_participants={RECORD_MIN_PARTICIPANTS} "
             f"max_recording_duration={RECORDING_MAX_DURATION_SECONDS}s "
             f"grace={STOP_GRACE_DELAY}s "
             f"telemetry_idle_timeout={TELEMETRY_IDLE_TIMEOUT}s")


@app.on_event("shutdown")
async def shutdown():
    for task in (_participant_sweeper, _recording_limiter):
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    if r:
        await r.aclose()


async def _enforce_recording_duration_limit():
    terminal_statuses = [
        "completed", "failed", "stopped",
        "EGRESS_COMPLETE", "EGRESS_FAILED", "EGRESS_ABORTED",
    ]
    reconciled_existing = False
    while True:
        now = datetime.now(UTC)
        try:
            if not reconciled_existing:
                unbounded = col.find(
                    {
                        "egress_id": {"$exists": True},
                        "stop_deadline_at": {"$exists": False},
                        "status": {"$nin": terminal_statuses},
                    },
                    {"_id": 0, "egress_id": 1, "started_at": 1},
                )
                async for recording in unbounded:
                    started_at = recording.get("started_at")
                    if isinstance(started_at, str):
                        try:
                            started_at = datetime.fromisoformat(
                                started_at.replace("Z", "+00:00")
                            )
                        except ValueError:
                            started_at = None
                    if isinstance(started_at, datetime):
                        if started_at.tzinfo is None:
                            started_at = started_at.replace(tzinfo=UTC)
                    else:
                        started_at = now
                    await col.update_one(
                        {
                            "egress_id": recording["egress_id"],
                            "stop_deadline_at": {"$exists": False},
                            "status": {"$nin": terminal_statuses},
                        },
                        {"$set": {
                            "stop_deadline_at": started_at + timedelta(
                                seconds=RECORDING_MAX_DURATION_SECONDS
                            ),
                            "max_duration_seconds": RECORDING_MAX_DURATION_SECONDS,
                        }},
                    )
                reconciled_existing = True

            expired = await col.find(
                {
                    "stop_deadline_at": {"$lte": now},
                    "status": {"$nin": terminal_statuses},
                    "mobile_stop_requested_at": {"$exists": False},
                    "$or": [
                        {"duration_stop_retry_after": {"$exists": False}},
                        {"duration_stop_retry_after": {"$lte": now}},
                    ],
                },
                {"_id": 0, "egress_id": 1, "room_name": 1},
            ).to_list(50)

            for recording in expired:
                egress_id = recording.get("egress_id")
                if not egress_id:
                    continue
                retry_after = now + timedelta(seconds=RECORDING_LIMITER_RETRY_SECONDS)
                claimed = await col.update_one(
                    {
                        "egress_id": egress_id,
                        "stop_deadline_at": {"$lte": now},
                        "status": {"$nin": terminal_statuses},
                        "mobile_stop_requested_at": {"$exists": False},
                        "$or": [
                            {"duration_stop_retry_after": {"$exists": False}},
                            {"duration_stop_retry_after": {"$lte": now}},
                        ],
                    },
                    {"$set": {
                        "duration_stop_retry_after": retry_after,
                        "duration_stop_reason": "maximum_duration",
                    }},
                )
                if claimed.modified_count != 1:
                    continue

                lk = lkapi.LiveKitAPI(LIVEKIT_URL, API_KEY, API_SECRET)
                try:
                    await lk.egress.stop_egress(
                        proto_egress.StopEgressRequest(egress_id=egress_id)
                    )
                    log.warning(
                        "Stopped Egress at maximum duration (%ss): %s room=%s",
                        RECORDING_MAX_DURATION_SECONDS,
                        egress_id,
                        recording.get("room_name"),
                    )
                except Exception:
                    log.exception(
                        "Unable to stop Egress at maximum duration: %s", egress_id
                    )
                finally:
                    await lk.aclose()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Recording duration limiter failed")
        await asyncio.sleep(RECORDING_LIMITER_INTERVAL)


def _participant_joined_at(participant) -> float | None:
    joined_at_ms = getattr(participant, "joined_at_ms", 0)
    if joined_at_ms:
        return float(joined_at_ms) / 1000
    joined_at = getattr(participant, "joined_at", 0)
    if joined_at:
        return float(joined_at)
    return None


def _telemetry_activity_at(document: dict) -> float | None:
    inserted_at = document.get("inserted_at")
    if isinstance(inserted_at, datetime):
        if inserted_at.tzinfo is None:
            inserted_at = inserted_at.replace(tzinfo=UTC)
        return inserted_at.timestamp()
    for field in ("server_ts", "ts"):
        value = document.get(field)
        if value is not None:
            try:
                return float(value) / 1000
            except (TypeError, ValueError, OverflowError):
                continue
    return None


async def _sweep_inactive_participants():
    while True:
        lk = None
        try:
            lk = lkapi.LiveKitAPI(LIVEKIT_URL, API_KEY, API_SECRET)
            rooms = await lk.room.list_rooms(proto_room.ListRoomsRequest())
            now = time.time()
            live_participants_by_room = {}
            live_roles_by_room = {}
            joined_at_by_room = {}
            for room in rooms.rooms:
                participants = await lk.room.list_participants(
                    proto_room.ListParticipantsRequest(room=room.name))
                live_participants = {
                    participant.identity for participant in participants.participants
                }
                live_participants_by_room[room.name] = live_participants
                live_roles = {}
                joined_at_values = {}
                state = _room(room.name)
                for participant in participants.participants:
                    identity = participant.identity
                    if _is_internal(identity):
                        continue

                    joined_at = _participant_joined_at(participant)
                    if joined_at is None:
                        joined_at = state["joined_at"].get(identity)
                    if joined_at is not None:
                        joined_at_values[identity] = joined_at

                    latest = await telemetry_col.find_one(
                        {"room_name": room.name, "participant": identity},
                        {"_id": 0, "inserted_at": 1, "server_ts": 1, "ts": 1,
                         "telemetry.role": 1},
                        sort=[("inserted_at", -1), ("server_ts", -1)],
                    )
                    if latest:
                        role = str(
                            (latest.get("telemetry") or {}).get("role", "trainee")
                        ).strip().casefold()
                        telemetry_activity = _telemetry_activity_at(latest)
                        if (role in RECORDING_ROLES or role == "observer") and (
                                joined_at is None or telemetry_activity is None
                                or telemetry_activity >= joined_at):
                            live_roles[identity] = role
                    else:
                        role = None
                        telemetry_activity = None

                    if role != "trainee":
                        continue

                    activity_times = [
                        activity for activity in (
                            telemetry_activity, joined_at)
                        if activity is not None
                    ]
                    if (not activity_times
                            or now - max(activity_times) < TELEMETRY_IDLE_TIMEOUT):
                        continue

                    log.warning(
                        "Expelling inactive trainee %s from room %s "
                        "(no telemetry for at least %ss)",
                        identity, room.name, TELEMETRY_IDLE_TIMEOUT,
                    )
                    await lk.room.remove_participant(
                        proto_room.RoomParticipantIdentity(
                            room=room.name, identity=identity))
                    live_participants.discard(identity)

                live_roles_by_room[room.name] = live_roles
                joined_at_by_room[room.name] = joined_at_values

            stored_rooms = await rooms_col.find(
                {"finished": {"$ne": True}},
                {"_id": 0, "room_name": 1, "participants": 1, "count": 1},
            ).to_list(None)
            room_names = set(live_participants_by_room)
            room_names.update(
                document["room_name"] for document in stored_rooms
                if document.get("room_name"))
            stored_by_name = {
                document["room_name"]: document for document in stored_rooms
                if document.get("room_name")
            }
            for room_name in room_names:
                live_participants = live_participants_by_room.get(room_name, set())
                stored_participants = set(
                    stored_by_name.get(room_name, {}).get("participants") or [])
                state = _room(room_name)
                previous_count = _count(room_name)
                state["participants"] = set(live_participants)
                previous_roles = state["roles"]
                current_roles = live_roles_by_room.get(room_name, {})
                state["roles"] = {
                    identity: current_roles.get(identity, previous_roles.get(identity))
                    for identity in live_participants
                    if not _is_internal(identity)
                    and current_roles.get(identity, previous_roles.get(identity))
                    in RECORDING_ROLES | {"observer"}
                }
                state["joined_at"] = {
                    identity: joined_at for identity, joined_at
                    in {**state["joined_at"],
                        **joined_at_by_room.get(room_name, {})}.items()
                    if identity in live_participants
                }
                current_count = _count(room_name)

                if (stored_participants != live_participants
                        or stored_by_name.get(room_name, {}).get("count") != current_count):
                    await rooms_col.update_one(
                        {"room_name": room_name},
                        {"$set": {
                            "room_name": room_name,
                            "participants": sorted(live_participants),
                            "count": current_count,
                            "updated_at": datetime.now(UTC),
                        }},
                        upsert=True,
                    )

                if RECORDING_MODE == "automatic" and current_count >= RECORD_MIN_PARTICIPANTS:
                    await _cancel_stop(room_name)
                    asyncio.create_task(_auto_start_recording(room_name))
                elif (RECORDING_MODE == "automatic"
                      and previous_count >= RECORD_MIN_PARTICIPANTS
                      and state["egress_ids"]):
                    await _schedule_stop(room_name)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Inactive participant sweep failed")
        finally:
            if lk:
                await lk.aclose()
        await asyncio.sleep(PARTICIPANT_SWEEP_INTERVAL)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _room(name: str) -> dict:
    if name not in _state:
        _state[name] = {
            "participants": set(),
            "egress_ids": set(),
            "roles": {},
            "joined_at": {},
            "starting": False,
            "stop_task": None,
        }
    return _state[name]


def _is_internal(identity: str) -> bool:
    return str(identity or "").strip().casefold() in INTERNAL_IDENTITIES


def _count(room_name: str) -> int:
    """Count connected coach/trainee users, excluding service identities."""
    st = _state.get(room_name)
    if not st:
        return 0
    return sum(
        identity in st["participants"] and not _is_internal(identity)
        and st["roles"].get(identity) in RECORDING_ROLES
        for identity in st["roles"]
    )


async def _refresh_participant_role(room_name: str, identity: str):
    if _is_internal(identity):
        return

    state = _room(room_name)
    joined_at = state["joined_at"].get(identity)
    latest = await telemetry_col.find_one(
        {"room_name": room_name, "participant": identity},
        {"_id": 0, "inserted_at": 1, "server_ts": 1, "ts": 1,
         "telemetry.role": 1},
        sort=[("inserted_at", -1), ("server_ts", -1)],
    )
    if not latest:
        state["roles"].pop(identity, None)
        return

    activity = _telemetry_activity_at(latest)
    if joined_at is not None and (activity is None or activity < joined_at):
        state["roles"].pop(identity, None)
        return

    role = str((latest.get("telemetry") or {}).get("role") or "").strip().casefold()
    if role in RECORDING_ROLES or role == "observer":
        state["roles"][identity] = role
    else:
        state["roles"].pop(identity, None)


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
    if RECORDING_MODE != "automatic":
        return
    if _count(room_name) < RECORD_MIN_PARTICIPANTS:
        return
    if st["egress_ids"] or st["starting"]:
        log.info(f"ℹ️  Enregistrement deja actif pour '{room_name}'")
        return

    st["starting"] = True
    lk = None
    try:
        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        lk = lkapi.LiveKitAPI(LIVEKIT_URL, API_KEY, API_SECRET)
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
        if _count(room_name) < RECORD_MIN_PARTICIPANTS:
            await _schedule_stop(room_name)
        log.info(
            "🔴 Enregistrement AUTO demarre | %s room=%s "
            "(%s coach/trainee users)",
            info.egress_id, room_name, _count(room_name),
        )

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
        st["starting"] = False
        if lk:
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
    auth_header = request.headers.get("authorization")
    if not auth_header:
        raise HTTPException(status_code=401, detail="Missing LiveKit webhook authorization.")
    try:
        receiver = lkapi.WebhookReceiver(API_KEY, API_SECRET)
        await asyncio.to_thread(receiver.receive, body, auth_header)
    except Exception as error:
        log.warning("Rejected invalid LiveKit webhook: %s", error)
        raise HTTPException(status_code=401, detail="Invalid LiveKit webhook signature.") from error
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

    state = _room(room_name)
    state["participants"].add(identity)
    state["joined_at"][identity] = time.time()
    state["roles"].pop(identity, None)
    if not _is_internal(identity):
        await _refresh_participant_role(room_name, identity)
    count = _count(room_name)
    log.info(
        "👤 Rejoint | %s | %s coach/trainee user(s) in '%s'",
        identity, count, room_name,
    )

    # Seuls les coachs/trainees peuvent maintenir ou declencher l'enregistrement.
    if RECORDING_MODE == "automatic" and count >= RECORD_MIN_PARTICIPANTS:
        await _cancel_stop(room_name)
    await _persist_room(room_name)

    await r.publish("room:events", json.dumps({
        "type": "participant_joined", "room_name": room_name,
        "identity": identity, "count": count,
    }))

    # ── Declenchement automatique de l'enregistrement ──────────────────────
    if RECORDING_MODE == "automatic" and count >= RECORD_MIN_PARTICIPANTS:
        log.info(f"🎯 Seuil atteint ({count}/{RECORD_MIN_PARTICIPANTS}) "
                 f"→ demarrage enregistrement")
        asyncio.create_task(_auto_start_recording(room_name))


async def handle_left(event: dict):
    p         = event.get("participant", {})
    room      = event.get("room", {})
    identity  = p.get("identity", "unknown")
    room_name = room.get("name", "")

    state = _room(room_name)
    previous_count = _count(room_name)
    state["participants"].discard(identity)
    state["roles"].pop(identity, None)
    state["joined_at"].pop(identity, None)
    count = _count(room_name)
    log.info(
        "👤 Parti | %s | %s coach/trainee user(s) in '%s'",
        identity, count, room_name,
    )

    await _persist_room(room_name)
    await r.publish("room:events", json.dumps({
        "type": "participant_left", "room_name": room_name,
        "identity": identity, "count": count,
    }))

    st = _room(room_name)
    if (RECORDING_MODE == "automatic"
            and previous_count >= RECORD_MIN_PARTICIPANTS
            and count < RECORD_MIN_PARTICIPANTS and st["egress_ids"]):
        log.info(f"📉 Moins de {RECORD_MIN_PARTICIPANTS} participants "
                 f"→ arret dans {STOP_GRACE_DELAY}s")
        await _schedule_stop(room_name)


# ── Handlers egress ────────────────────────────────────────────────────────────

async def handle_egress_started(event: dict):
    eg        = event.get("egressInfo", {})
    egress_id = eg.get("egressId", "")
    room_name = eg.get("roomName", "")
    try:
        started_ns = int(eg.get("startedAt", 0))
        started_at = (
            datetime.fromtimestamp(started_ns / 1_000_000_000, UTC)
            if started_ns > 0 else datetime.now(UTC)
        )
    except (TypeError, ValueError, OverflowError, OSError):
        started_at = datetime.now(UTC)
    stop_deadline_at = started_at + timedelta(
        seconds=RECORDING_MAX_DURATION_SECONDS
    )
    _room(room_name)["egress_ids"].add(egress_id)
    await col.update_one({"egress_id": egress_id}, {"$set": {
        "egress_id": egress_id, "room_name": room_name,
        "status": "recording",
        "started_at": started_at.isoformat(),
        "stop_deadline_at": stop_deadline_at,
        "max_duration_seconds": RECORDING_MAX_DURATION_SECONDS,
        "files": [],
    }}, upsert=True)
    await rooms_col.update_one(
        {"room_name": room_name, "mobile_recording.egress_id": egress_id},
        {"$set": {"mobile_recording.status": "recording"}},
    )
    await r.publish("egress:events", json.dumps(
        {"type": "started", "egress_id": egress_id, "room_name": room_name}))
    log.info(f"▶  Egress demarre | {egress_id}")


async def handle_egress_updated(event: dict):
    eg = event.get("egressInfo", {})
    status = eg.get("status", "")
    await col.update_one({"egress_id": eg.get("egressId","")},
        {"$set": {"status": status,
                  "updated_at": datetime.now(UTC).isoformat()}})
    await rooms_col.update_one(
        {"room_name": eg.get("roomName", ""),
         "mobile_recording.egress_id": eg.get("egressId", "")},
        {"$set": {"mobile_recording.status": status}},
    )


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
    await rooms_col.update_one(
        {"room_name": room_name, "mobile_recording.egress_id": egress_id},
        {"$set": {"mobile_recording.status": update["status"]}},
    )

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
    state = _room(name)
    state["participants"].clear()
    state["roles"].clear()
    state["joined_at"].clear()
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
    st["roles"].clear()
    st["joined_at"].clear()
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
