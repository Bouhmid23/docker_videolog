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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [AGENT] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

LIVEKIT_URL = os.environ["LIVEKIT_URL"]
API_KEY = os.environ["LIVEKIT_API_KEY"]
API_SECRET = os.environ["LIVEKIT_API_SECRET"]
REDIS_URL = os.environ["REDIS_URL"]
ROOM_NAME = os.environ.get("ROOM_NAME", "poc-room")
ROOM_NAMES = [
    name.strip()
    for name in os.environ.get("ROOM_NAMES", ROOM_NAME).split(",")
    if name.strip()
]
STREAM_KEY = "telemetry:stream"
PACKET_VERSION = 2
PACKET_SIZE = 21
UINT32_MODULUS = 1 << 32

ALERT_HR_MAX = int(os.environ.get("ALERT_HR_MAX", "185"))
ALERT_HR_REST = int(os.environ.get("ALERT_HR_REST", "50"))
ALERT_SPEED_MAX = float(os.environ.get("ALERT_SPEED_MAX", "40"))

redis_client: aioredis.Redis | None = None
session_starts: dict[str, float] = {}
participant_counts: dict[str, dict[str, int]] = {}


def without_posture(value):
    if isinstance(value, dict):
        return {key: without_posture(item) for key, item in value.items()
                if str(key).casefold() != "posture"}
    if isinstance(value, list):
        return [without_posture(item) for item in value]
    return value


def stream_value(value) -> str:
    return "" if value is None else str(value)


async def connect_redis():
    global redis_client
    redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
    await redis_client.ping()
    log.info("Redis connected")


async def register_session(room_name: str):
    session_start_unix = time.time() * 1000
    session_starts[room_name] = session_start_unix
    await redis_client.hset(
        f"session:{room_name}",
        mapping={
            "room_name": room_name,
            "started_at": datetime.now(UTC).isoformat(),
            "start_unix": session_start_unix,
        },
    )
    log.info("Session registered: %s", room_name)


def compute_pts(room_name: str, client_ts_ms: float) -> float:
    session_start_unix = session_starts.get(room_name)
    if session_start_unix is None:
        return 0.0
    return max(0.0, client_ts_ms - session_start_unix)


def decode_binary_packet(packet_bytes: bytes, received_ms: float) -> dict | None:
    """Decode the mobile TelemetryCodec v2 packet (21 bytes, big-endian)."""
    if len(packet_bytes) < PACKET_SIZE:
        return None
    try:
        (version, role_code, speed_raw, hr_raw, cadence_raw, lat_raw,
         lng_raw, altitude_raw, bearing_raw, timestamp_raw) = struct.unpack_from(
            ">BBHBBiihBI", packet_bytes
        )
    except struct.error:
        return None

    if version != PACKET_VERSION:
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

    return {
        "role": role,
        "speed": None if speed_raw == 65535 else speed_raw / 100.0,
        "heart_rate": None if hr_raw == 255 else hr_raw,
        "cadence": None if cadence_raw == 255 else cadence_raw,
        "lat": None if lat_raw == 0x7FFFFFFF else lat_raw / 10_000_000.0,
        "lng": None if lng_raw == 0x7FFFFFFF else lng_raw / 10_000_000.0,
        "altitude": None if altitude_raw == 0x7FFF else altitude_raw / 100.0,
        "bearing": None if bearing_raw == 255 else bearing_raw,
        "capture_ts_ms": capture_ts_ms,
    }


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
    gps = payload.get("gps", {})
    lat = payload.get("lat", gps.get("lat", 0))
    lng = payload.get("lng", gps.get("lng", 0))
    altitude = payload.get("altitude", gps.get("altitude", 0))

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
        "lat": stream_value(lat),
        "lng": stream_value(lng),
        "altitude": stream_value(altitude),
        "raw": json.dumps(payload),
    }
    if raw is None:
        event.update({
            "packet_encoding": "base64",
            "packet_b64": base64.b64encode(packet_bytes).decode("ascii"),
            "telemetry_decoded": "true" if binary_telemetry else "false",
        })

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


async def maybe_emit_alerts(room_name: str, identity: str, event: dict, pts_ms: float, client_ts: float):
    hr = int(float(event["heart_rate"])) if event["heart_rate"] else 0
    speed = float(event["speed"]) if event["speed"] else 0

    if hr > ALERT_HR_MAX:
        await emit_alert(
            room_name,
            identity,
            pts_ms,
            client_ts,
            "hr_max",
            hr,
            f"FC {hr}bpm > seuil {ALERT_HR_MAX}bpm",
        )
    elif 0 < hr < ALERT_HR_REST:
        await emit_alert(
            room_name,
            identity,
            pts_ms,
            client_ts,
            "hr_low",
            hr,
            f"FC {hr}bpm < seuil repos {ALERT_HR_REST}bpm",
        )

    if speed > ALERT_SPEED_MAX:
        await emit_alert(
            room_name,
            identity,
            pts_ms,
            client_ts,
            "speed_max",
            speed,
            f"Vitesse {speed}km/h > seuil {ALERT_SPEED_MAX}km/h",
        )


async def emit_alert(room_name: str, identity: str, pts_ms: float, ts: float, alert_type: str, value, message: str):
    alert = {
        "room_name": room_name,
        "participant": identity,
        "pts_ms": str(pts_ms),
        "type": alert_type,
        "value": str(value),
        "message": message,
        "ts": str(ts),
    }
    await redis_client.xadd("telemetry:alerts", alert, maxlen=10000, approximate=True)
    await redis_client.publish("alerts", json.dumps(alert))
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


async def run_room(room_name: str):
    await register_session(room_name)

    backoff = 1
    while True:
        room = None
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
        except Exception as e:
            log.error("[%s] Connection error: %s", room_name, e)
        finally:
            if room:
                try:
                    await room.disconnect()
                except Exception:
                    pass

        log.info("[%s] Reconnecting in %ss...", room_name, backoff)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 30)


async def main():
    await connect_redis()
    log.info("Watching rooms: %s", ", ".join(ROOM_NAMES))
    await asyncio.gather(*(run_room(room_name) for room_name in ROOM_NAMES))


if __name__ == "__main__":
    asyncio.run(main())
