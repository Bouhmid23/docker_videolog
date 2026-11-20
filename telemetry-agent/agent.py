"""
Telemetry Agent - Videolog POC (Sport Coach)
Connects to one or more LiveKit rooms, listens for DataTracks topic="telemetry",
enriches events with PTS, and publishes them to Redis Streams.

Configuration:
  ROOM_NAME=poc-room                 # backward compatible single-room mode
  ROOM_NAMES=room-a,room-b,room-c    # multi-room mode
"""
import asyncio
import base64
import json
import logging
import os
import struct
import time
from datetime import UTC, datetime

import redis.asyncio as aioredis
from livekit import api, rtc

try:
    from motor.motor_asyncio import AsyncIOMotorClient
except ImportError:
    AsyncIOMotorClient = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [AGENT] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

LIVEKIT_URL = os.environ.get("LIVEKIT_URL", "ws://livekit:7880")
API_KEY = os.environ.get("LIVEKIT_API_KEY", "devkey")
API_SECRET = os.environ.get("LIVEKIT_API_SECRET", "secret")
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379")
MONGO_URL = os.environ.get("MONGO_URL", "")
MONGO_DB_NAME = os.environ.get("MONGO_DB_NAME", "videolog")
ROOM_NAME = os.environ.get("ROOM_NAME", "")
ROOM_NAMES = [
    name.strip()
    for name in os.environ.get("ROOM_NAMES", ROOM_NAME).split(",")
    if name.strip()
]
STREAM_KEY = "telemetry:stream"
PACKET_VERSION = 2
PACKET_SIZE = 21
PACKET_VERSION_V3 = 3
PACKET_SIZE_V3 = 25
UINT32_MODULUS = 1 << 32

ALERT_HR_MAX = int(os.environ.get("ALERT_HR_MAX", "185"))
ALERT_HR_REST = int(os.environ.get("ALERT_HR_REST", "50"))
ALERT_SPEED_MAX = float(os.environ.get("ALERT_SPEED_MAX", "40"))
ALERT_COOLDOWN_SECONDS = float(os.environ.get("ALERT_COOLDOWN_SECONDS", "30"))
CACHE_TTL_SECONDS = 300.0

redis_client: aioredis.Redis | None = None
mongo_client = None
users_col = None
threshold_history_col = None
session_starts: dict[str, float] = {}
participant_counts: dict[str, dict[str, int]] = {}
active_rooms: dict[str, asyncio.Task] = {}
_athlete_thresholds_cache: dict[str, tuple[int, int, float]] = {}


def without_posture(value):
    if isinstance(value, dict):
        return {key: without_posture(item) for key, item in value.items()
                if str(key).casefold() != "posture"}
    if isinstance(value, list):
        return [without_posture(item) for item in value]
    return value


def get_http_livekit_url(url: str) -> str:
    """Convert ws:// or wss:// URL to http:// or https:// for LiveKitAPI."""
    if url.startswith("ws://"):
        return "http://" + url[5:]
    if url.startswith("wss://"):
        return "https://" + url[6:]
    return url


def stream_value(value) -> str:
    return "" if value is None else str(value)


async def connect_redis():
    global redis_client
    redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
    await redis_client.ping()
    log.info("Redis connected")


async def connect_mongo():
    global mongo_client, users_col, threshold_history_col
    if not MONGO_URL:
        log.info("MONGO_URL not set; skipping direct MongoDB connection")
        return
    if AsyncIOMotorClient is None:
        log.warning("motor library not installed; skipping MongoDB connection")
        return
    try:
        mongo_client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=2000)
        db = mongo_client[MONGO_DB_NAME]
        users_col = db.users
        threshold_history_col = db.threshold_history
        log.info("MongoDB connected for athlete profile queries")
    except Exception as e:
        log.warning("Could not connect to MongoDB at %s: %s", MONGO_URL, e)


async def register_session(room_name: str):
    existing_start: float | None = None
    if redis_client:
        try:
            raw_start = await redis_client.hget(f"session:{room_name}", "start_unix")
            if raw_start is not None:
                parsed = float(raw_start)
                if parsed > 0:
                    existing_start = parsed
        except Exception as e:
            log.debug("Could not read existing start_unix for %s: %s", room_name, e)

    session_start_unix = existing_start if existing_start is not None else time.time() * 1000
    session_starts[room_name] = session_start_unix
    if redis_client:
        try:
            mapping = {
                "room_name": room_name,
                "start_unix": session_start_unix,
            }
            if existing_start is None:
                mapping["started_at"] = datetime.now(UTC).isoformat()
            await redis_client.hset(f"session:{room_name}", mapping=mapping)
            log.info(
                "Session registered: %s (resumed=%s)",
                room_name,
                existing_start is not None,
            )
        except Exception as e:
            log.warning("Could not register session in Redis: %s", e)


def compute_pts(room_name: str, client_ts_ms: float) -> float:
    session_start_unix = session_starts.get(room_name)
    if session_start_unix is None:
        return 0.0
    return max(0.0, client_ts_ms - session_start_unix)


def decode_binary_packet(packet_bytes: bytes, received_ms: float) -> dict | None:
    """Decode the mobile TelemetryCodec v2 (21 bytes) or v3 (25 bytes, big-endian)."""
    if not packet_bytes:
        return None
    version = packet_bytes[0]
    if version == PACKET_VERSION:
        if len(packet_bytes) < PACKET_SIZE:
            return None
        try:
            (_, role_code, speed_raw, hr_raw, cadence_raw, lat_raw,
             lng_raw, altitude_raw, bearing_raw, timestamp_raw) = struct.unpack_from(
                ">BBHBBiihBI", packet_bytes
            )
        except struct.error:
            return None
        bearing = None if bearing_raw == 255 else bearing_raw
        power_w = None
    elif version == PACKET_VERSION_V3:
        if len(packet_bytes) < PACKET_SIZE_V3:
            return None
        try:
            (_, role_code, speed_raw, hr_raw, cadence_raw, lat_raw,
             lng_raw, altitude_raw, bearing_raw, power_raw, timestamp_raw, _flags) = struct.unpack_from(
                ">BBHBBiihHHIB", packet_bytes
            )
        except struct.error:
            return None
        bearing = None if bearing_raw == 65535 else bearing_raw
        power_w = None if power_raw == 65535 else power_raw
    else:
        return None

    role = {0: "coach", 1: "trainee", 2: "observer"}.get(role_code)
    if role is None:
        return None

    # The uint32 timestamp wraps roughly every 49.7 days. Expand it to the
    # epoch value nearest the receive time so it remains comparable to Egress.
    received_mod = int(received_ms) % UINT32_MODULUS
    delta_ms = ((timestamp_raw - received_mod + UINT32_MODULUS // 2)
                % UINT32_MODULUS) - UINT32_MODULUS // 2
    capture_ts_ms = received_ms + delta_ms

    decoded = {
        "role": role,
        "speed": None if speed_raw == 65535 else speed_raw / 100.0,
        "heart_rate": None if hr_raw == 255 else hr_raw,
        "cadence": None if cadence_raw == 255 else cadence_raw,
        "lat": None if lat_raw == 0x7FFFFFFF else lat_raw / 10_000_000.0,
        "lng": None if lng_raw == 0x7FFFFFFF else lng_raw / 10_000_000.0,
        "altitude": None if altitude_raw == 0x7FFF else altitude_raw / 100.0,
        "bearing": bearing,
        "capture_ts_ms": capture_ts_ms,
    }
    if power_w is not None:
        decoded["power_w"] = power_w
    return decoded


async def on_data_received(room_name: str, packet: rtc.DataPacket):
    if getattr(packet, "topic", None) != "telemetry":
        return

    identity = packet.participant.identity if packet.participant else "unknown"
    server_ts = time.time() * 1000
    packet_bytes = bytes(packet.data)
    try:
        raw = json.loads(packet_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        # Preserve the mobile packet byte-for-byte (base64 is needed because
        # Redis Streams with decode_responses=True only accept text fields).
        raw = None

    binary_telemetry = None
    if raw is not None and not isinstance(raw, dict):
        raw = None

    if raw is None:
        binary_telemetry = decode_binary_packet(packet_bytes, server_ts)
    elif raw.get("type") != "telemetry":
        return

    client_ts = (
        raw.get("ts", server_ts) if raw is not None
        else binary_telemetry["capture_ts_ms"] if binary_telemetry else server_ts
    )
    pts_ms = compute_pts(room_name, client_ts)
    payload = raw.get("payload", {}) if raw is not None else (binary_telemetry or {})
    if not isinstance(payload, dict):
        payload = {}
    payload = {key: value for key, value in payload.items() if key != "capture_ts_ms"}
    # Posture is deliberately excluded from telemetry events and storage.
    payload = without_posture(payload)
    gps = payload.get("gps", {}) if isinstance(payload.get("gps"), dict) else {}
    lat = payload.get("lat", gps.get("lat", 0))
    lng = payload.get("lng", gps.get("lng", 0))
    altitude = payload.get("altitude", gps.get("altitude", 0))
    bearing = payload.get("bearing", gps.get("bearing"))
    power_w = payload.get("power_w")

    room_counts = participant_counts.setdefault(room_name, {})
    room_counts[identity] = room_counts.get(identity, 0) + 1

    event = {
        "room_name": room_name,
        "participant": identity,
        "ts": str(client_ts),
        "pts_ms": str(pts_ms),
        "server_ts": str(server_ts),
        "capture_ts": str(client_ts),
        "role": str(payload.get("role", "trainee")),
        "speed": stream_value(payload.get("speed", 0)),
        "heart_rate": stream_value(payload.get("heart_rate", 0)),
        "cadence": stream_value(payload.get("cadence", 0)),
        "power_w": stream_value(power_w),
        "lat": stream_value(lat),
        "lng": stream_value(lng),
        "altitude": stream_value(altitude),
        "bearing": stream_value(bearing),
        "raw": json.dumps(payload),
    }
    if raw is None:
        event.update({
            "packet_encoding": "base64",
            "packet_b64": base64.b64encode(packet_bytes).decode("ascii"),
            "telemetry_decoded": "true" if binary_telemetry else "false",
        })

    if redis_client:
        await redis_client.xadd(STREAM_KEY, event, maxlen=100000, approximate=True)
    if raw is not None or binary_telemetry:
        await maybe_emit_alerts(room_name, identity, event, pts_ms, client_ts)

    count = room_counts[identity]
    if count % 20 == 0:
        log.info(
            "[%s] [%s] %s speed=%skm/h hr=%sbpm cadence=%srpm pts=%.0fms",
            room_name,
            event["role"],
            identity,
            event["speed"],
            event["heart_rate"],
            event["cadence"],
            pts_ms,
        )


async def get_athlete_thresholds(identity: str) -> tuple[int, int]:
    """Retrieve personalized HR alert thresholds for an athlete.

    Verifies GDPR health consent (users.health_consent.granted == True).
    Strict fallback to (ALERT_HR_MAX, ALERT_HR_REST) if consent is missing/false,
    profile data is absent, or database is unavailable.
    """
    raw_id = (identity or "").strip()
    clean_id = raw_id[:-4] if raw_id.endswith(":web") else raw_id
    if not clean_id or clean_id in ("unknown", "telemetry-agent") or clean_id.upper().startswith("EG_"):
        return ALERT_HR_MAX, ALERT_HR_REST

    now = time.monotonic()

    # 1. Vérification Cache Niveau 1 (Mémoire locale)
    cached = _athlete_thresholds_cache.get(clean_id)
    if cached and now < cached[2]:
        return cached[0], cached[1]

    # 2. Vérification Cache Niveau 2 (Redis)
    redis_key = f"athlete:thresholds:{clean_id}"
    if redis_client:
        try:
            cached_json = await redis_client.get(redis_key)
            if cached_json:
                data = json.loads(cached_json)
                hr_max = int(data.get("hr_max", ALERT_HR_MAX))
                hr_rest = int(data.get("hr_rest", ALERT_HR_REST))
                _athlete_thresholds_cache[clean_id] = (hr_max, hr_rest, now + CACHE_TTL_SECONDS)
                return hr_max, hr_rest
        except Exception as e:
            log.debug("Redis cache check error for athlete %s: %s", clean_id, e)

    # 3. Interrogation MongoDB (users_col + repli threshold_history_col si consenti)
    hr_max = ALERT_HR_MAX
    hr_rest = ALERT_HR_REST

    if users_col is not None:
        try:
            doc = await users_col.find_one(
                {"_id": clean_id},
                {"athlete_profile": 1, "health_consent": 1},
            )
            if doc:
                consent = doc.get("health_consent")
                if isinstance(consent, dict) and consent.get("granted") is True:
                    profile = doc.get("athlete_profile")
                    if isinstance(profile, dict):
                        if profile.get("hr_max") is not None:
                            try:
                                val = int(profile["hr_max"])
                                if val > 0:
                                    hr_max = val
                            except (ValueError, TypeError):
                                hr_max = ALERT_HR_MAX
                        if profile.get("hr_rest") is not None:
                            try:
                                val = int(profile["hr_rest"])
                                if val > 0:
                                    hr_rest = val
                            except (ValueError, TypeError):
                                hr_rest = ALERT_HR_REST
                    if threshold_history_col is not None:
                        try:
                            thr = await threshold_history_col.find_one(
                                {"uid": clean_id},
                                {"hr_max": 1, "hr_rest": 1},
                                sort=[("effective_from", -1), ("created_at", -1)],
                            )
                            if isinstance(thr, dict):
                                if thr.get("hr_max") is not None:
                                    val = int(thr["hr_max"])
                                    if val > 0:
                                        hr_max = val
                                if thr.get("hr_rest") is not None:
                                    val = int(thr["hr_rest"])
                                    if val > 0:
                                        hr_rest = val
                        except Exception:
                            pass
        except Exception as e:
            log.warning("MongoDB query failed for athlete %s: %s; using fallbacks", clean_id, e)

    # Mise en cache L1 et L2
    _athlete_thresholds_cache[clean_id] = (hr_max, hr_rest, now + CACHE_TTL_SECONDS)
    if redis_client:
        try:
            await redis_client.set(
                redis_key,
                json.dumps({"hr_max": hr_max, "hr_rest": hr_rest}),
                ex=int(CACHE_TTL_SECONDS),
            )
        except Exception as e:
            log.debug("Failed to set Redis threshold cache: %s", e)

    return hr_max, hr_rest


def _can_emit_alert(room_name: str, identity: str, alert_type: str) -> bool:
    if ALERT_COOLDOWN_SECONDS <= 0:
        return True
    cooldowns = participant_counts.setdefault(f"__cooldown__:{room_name}", {})
    key = f"{identity}:{alert_type}"
    now = time.monotonic()
    last_ts = cooldowns.get(key)
    if last_ts is not None and (now - last_ts) < ALERT_COOLDOWN_SECONDS:
        return False
    cooldowns[key] = now
    return True


async def maybe_emit_alerts(room_name: str, identity: str, event: dict, pts_ms: float, client_ts: float):
    try:
        hr = int(float(event["heart_rate"])) if event.get("heart_rate") else 0
    except (ValueError, TypeError):
        hr = 0
    try:
        speed = float(event["speed"]) if event.get("speed") else 0.0
    except (ValueError, TypeError):
        speed = 0.0

    hr_max, hr_rest = await get_athlete_thresholds(identity)

    if hr > hr_max:
        if _can_emit_alert(room_name, identity, "hr_max"):
            await emit_alert(
                room_name,
                identity,
                pts_ms,
                client_ts,
                "hr_max",
                hr,
                f"FC {hr}bpm > seuil {hr_max}bpm",
                threshold=hr_max,
            )
    elif 0 < hr < hr_rest:
        if _can_emit_alert(room_name, identity, "hr_low"):
            await emit_alert(
                room_name,
                identity,
                pts_ms,
                client_ts,
                "hr_low",
                hr,
                f"FC {hr}bpm < seuil repos {hr_rest}bpm",
                threshold=hr_rest,
            )

    if speed > ALERT_SPEED_MAX:
        if _can_emit_alert(room_name, identity, "speed_max"):
            await emit_alert(
                room_name,
                identity,
                pts_ms,
                client_ts,
                "speed_max",
                speed,
                f"Vitesse {speed}km/h > seuil {ALERT_SPEED_MAX}km/h",
                threshold=ALERT_SPEED_MAX,
            )


async def emit_alert(
    room_name: str,
    identity: str,
    pts_ms: float,
    ts: float,
    alert_type: str,
    value,
    message: str,
    threshold=None,
):
    alert = {
        "room_name": room_name,
        "participant": identity,
        "pts_ms": str(pts_ms),
        "type": alert_type,
        "value": str(value),
        "message": message,
        "ts": str(ts),
    }
    if threshold is not None:
        alert["threshold"] = str(threshold)
    if redis_client:
        try:
            await redis_client.xadd("telemetry:alerts", alert, maxlen=10000, approximate=True)
            await redis_client.publish("alerts", json.dumps(alert))
        except Exception as e:
            log.warning("Could not publish alert to Redis: %s", e)
    log.warning("[%s] %s | %s | %s", room_name, alert_type.upper(), identity, message)


async def connect_room(room_name: str) -> rtc.Room:
    token = (
        api.AccessToken(API_KEY, API_SECRET)
        .with_identity("telemetry-agent")
        .with_name(f"Telemetry Agent ({room_name})")
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room_name,
                can_subscribe=True,
                can_publish=False,
            )
        )
        .to_jwt()
    )

    room = rtc.Room()

    @room.on("data_received")
    def _on_data(packet: rtc.DataPacket):
        asyncio.create_task(on_data_received(room_name, packet))

    @room.on("participant_connected")
    def _on_join(p: rtc.RemoteParticipant):
        log.info("[%s] Participant connected: %s", room_name, p.identity)

    @room.on("participant_disconnected")
    def _on_left(p: rtc.RemoteParticipant):
        log.info("[%s] Participant disconnected: %s", room_name, p.identity)
        participant_counts.setdefault(room_name, {}).pop(p.identity, None)

    await room.connect(LIVEKIT_URL, token)
    log.info("Agent connected to room '%s'", room_name)
    return room


def start_watching_room(room_name: str) -> asyncio.Task | None:
    clean_name = room_name.strip()
    if not clean_name:
        return None
    existing = active_rooms.get(clean_name)
    if existing and not existing.done():
        log.debug("Room '%s' already has an active watcher task", clean_name)
        return existing

    task = asyncio.create_task(run_room(clean_name), name=f"room:{clean_name}")
    active_rooms[clean_name] = task

    def _cleanup(t: asyncio.Task):
        if not t.cancelled() and t.exception():
            log.error("[%s] Room watcher task crashed: %s", clean_name, t.exception())
        if active_rooms.get(clean_name) is t:
            active_rooms.pop(clean_name, None)

    task.add_done_callback(_cleanup)
    log.info("Started dynamic watcher task for room '%s'", clean_name)
    return task

start_room_task = start_watching_room


async def stop_watching_room(room_name: str):
    clean_name = room_name.strip()
    task = active_rooms.pop(clean_name, None)
    if task and not task.done():
        log.info("Cancelling watcher task for room '%s'...", clean_name)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.warning("[%s] Error during room task cleanup: %s", clean_name, e)

    if clean_name not in active_rooms:
        session_starts.pop(clean_name, None)
        participant_counts.pop(clean_name, None)
    log.info("State cleaned up for room '%s'", clean_name)

stop_room_task = stop_watching_room


async def run_room(room_name: str):
    await register_session(room_name)

    backoff = 1
    room: rtc.Room | None = None
    try:
        while True:
            try:
                room = await connect_room(room_name)
                backoff = 1
                disconnected = asyncio.Event()

                @room.on("disconnected")
                def _on_disc(*args):
                    reason = args[0] if args else "no reason provided"
                    log.warning("[%s] Disconnected: %s", room_name, reason)
                    disconnected.set()

                await disconnected.wait()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.error("[%s] Connection error: %s", room_name, e)
            finally:
                if room:
                    try:
                        await room.disconnect()
                    except Exception:
                        pass
                    room = None

            log.info("[%s] Reconnecting in %ss...", room_name, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)
    except asyncio.CancelledError:
        log.info("[%s] Room watcher task cancelled, disconnecting cleanly", room_name)
        if room:
            try:
                await room.disconnect()
            except Exception:
                pass
        raise


async def reconcile_active_rooms():
    """Reconcile active rooms at startup via configured ROOM_NAMES and LiveKitAPI.room.list_rooms()."""
    for name in ROOM_NAMES:
        if name:
            start_watching_room(name)

    http_url = get_http_livekit_url(LIVEKIT_URL)
    lk: api.LiveKitAPI | None = None
    try:
        lk = api.LiveKitAPI(http_url, API_KEY, API_SECRET)
        res = await lk.room.list_rooms(api.ListRoomsRequest())
        count = 0
        for r in res.rooms:
            if r.name and r.name not in active_rooms:
                start_watching_room(r.name)
                count += 1
        log.info("Startup reconciliation complete: %d active rooms discovered from LiveKit", count)
    except Exception as e:
        log.warning("Could not reconcile active rooms from LiveKit at %s: %s", http_url, e)
    finally:
        if lk:
            try:
                await lk.aclose()
            except Exception:
                pass


async def listen_room_events():
    """Listen for room lifecycle events on Redis pubsub channel 'room:events'."""
    while True:
        pubsub = None
        try:
            if not redis_client:
                await asyncio.sleep(1)
                continue
            pubsub = redis_client.pubsub()
            await pubsub.subscribe("room:events")
            log.info("Subscribed to Redis channel 'room:events'")

            async for msg in pubsub.listen():
                if msg.get("type") != "message":
                    continue
                raw_data = msg.get("data")
                if not raw_data:
                    continue

                try:
                    payload = json.loads(raw_data)
                except (json.JSONDecodeError, TypeError):
                    continue

                event_type = payload.get("event") or payload.get("type")
                room_name = payload.get("room_name")
                if not room_name:
                    continue

                if event_type == "room_started":
                    log.info("Event 'room_started' received for room '%s'", room_name)
                    start_watching_room(room_name)
                elif event_type == "room_finished":
                    log.info("Event 'room_finished' received for room '%s'", room_name)
                    await stop_watching_room(room_name)
                else:
                    log.debug("Ignored event type '%s' on room:events", event_type)

        except asyncio.CancelledError:
            log.info("Redis room:events listener task cancelled")
            if pubsub:
                try:
                    await pubsub.unsubscribe("room:events")
                    await pubsub.close()
                except Exception:
                    pass
            raise
        except Exception as e:
            log.error("Error in listen_room_events: %s. Reconnecting in 3s...", e)
            await asyncio.sleep(3)
        finally:
            if pubsub:
                try:
                    await pubsub.close()
                except Exception:
                    pass


async def main():
    await connect_redis()
    await connect_mongo()
    await reconcile_active_rooms()
    listener_task = asyncio.create_task(listen_room_events(), name="redis:room:events")
    log.info("Dynamic telemetry agent running. Active rooms: %s", list(active_rooms.keys()))
    try:
        await listener_task
    except asyncio.CancelledError:
        log.info("Telemetry agent stopping...")
    finally:
        for room_name in list(active_rooms.keys()):
            await stop_watching_room(room_name)
        if mongo_client:
            mongo_client.close()
        if redis_client:
            await redis_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
