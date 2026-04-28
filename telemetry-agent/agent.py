"""
Telemetry Agent — Videolog POC (Sport Coach)
Rejoins la room LiveKit, ecoute les DataTracks topic="telemetry",
enrichit avec PTS, publie dans Redis Streams.

Champs attendus dans le payload:
  role        — "coach" | "trainee"
  speed       — km/h
  heart_rate  — bpm
  cadence     — rpm
  gps         — {lat, lng, altitude}
"""
import asyncio, json, os, time, logging
from datetime import datetime, UTC

import redis.asyncio as aioredis
from livekit import rtc, api

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [AGENT] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

LIVEKIT_URL = os.environ["LIVEKIT_URL"]
API_KEY     = os.environ["LIVEKIT_API_KEY"]
API_SECRET  = os.environ["LIVEKIT_API_SECRET"]
REDIS_URL   = os.environ["REDIS_URL"]
ROOM_NAME   = os.environ.get("ROOM_NAME", "poc-room")
STREAM_KEY  = "telemetry:stream"

# Seuils d'alertes sportives
ALERT_HR_MAX   = int(os.environ.get("ALERT_HR_MAX",   "185"))  # FC max
ALERT_HR_REST  = int(os.environ.get("ALERT_HR_REST",  "50"))   # FC trop basse
ALERT_SPEED_MAX = float(os.environ.get("ALERT_SPEED_MAX", "40"))  # km/h

redis_client:       aioredis.Redis = None
session_start_unix: float          = None
_participant_counts: dict          = {}


async def connect_redis():
    global redis_client
    redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
    await redis_client.ping()
    log.info("✅ Redis connecte")


async def register_session(room_name: str):
    global session_start_unix
    session_start_unix = time.time() * 1000
    await redis_client.hset(f"session:{room_name}", mapping={
        "room_name":  room_name,
        "started_at": datetime.now(UTC).isoformat(),
        "start_unix": session_start_unix,
    })
    log.info(f"📋 Session: {room_name}")


def compute_pts(client_ts_ms: float) -> float:
    if session_start_unix is None:
        return 0.0
    return max(0.0, client_ts_ms - session_start_unix)


async def on_data_received(packet: rtc.DataPacket):
    # Filtrer par topic
    if getattr(packet, "topic", None) != "telemetry":
        return

    try:
        raw = json.loads(packet.data.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        log.warning(f"Decode error: {e}")
        return

    if raw.get("type") != "telemetry":
        return

    identity  = packet.participant.identity if packet.participant else "unknown"
    client_ts = raw.get("ts", time.time() * 1000)
    pts_ms    = compute_pts(client_ts)
    payload   = raw.get("payload", {})
    gps       = payload.get("gps", {})

    _participant_counts[identity] = _participant_counts.get(identity, 0) + 1

    event = {
        "room_name":   ROOM_NAME,
        "participant": identity,
        "ts":          str(client_ts),
        "pts_ms":      str(pts_ms),
        "server_ts":   str(time.time() * 1000),
        # Champs sportifs
        "role":        str(payload.get("role", "trainee")),
        "speed":       str(payload.get("speed", 0)),
        "heart_rate":  str(payload.get("heart_rate", 0)),
        "cadence":     str(payload.get("cadence", 0)),
        "lat":         str(gps.get("lat", 0)),
        "lng":         str(gps.get("lng", 0)),
        "altitude":    str(gps.get("altitude", 0)),
        "raw":         json.dumps(payload),
    }

    await redis_client.xadd(STREAM_KEY, event)

    # ── Alertes sportives ────────────────────────────────────────────────────
    hr    = int(float(event["heart_rate"]))
    speed = float(event["speed"])

    if hr > ALERT_HR_MAX:
        await _emit_alert(identity, pts_ms, client_ts, "hr_max",
                          hr, f"FC {hr}bpm > seuil {ALERT_HR_MAX}bpm")
    elif hr < ALERT_HR_REST and hr > 0:
        await _emit_alert(identity, pts_ms, client_ts, "hr_low",
                          hr, f"FC {hr}bpm < seuil repos {ALERT_HR_REST}bpm")

    if speed > ALERT_SPEED_MAX:
        await _emit_alert(identity, pts_ms, client_ts, "speed_max",
                          speed, f"Vitesse {speed}km/h > seuil {ALERT_SPEED_MAX}km/h")

    count = _participant_counts[identity]
    if count % 20 == 0:
        log.info(f"📡 [{event['role']:8}] {identity} "
                 f"speed={speed}km/h hr={hr}bpm "
                 f"cadence={event['cadence']}rpm "
                 f"pts={pts_ms:.0f}ms")


async def _emit_alert(identity, pts_ms, ts, alert_type, value, message):
    alert = {
        "room_name":   ROOM_NAME,
        "participant": identity,
        "pts_ms":      str(pts_ms),
        "type":        alert_type,
        "value":       str(value),
        "message":     message,
        "ts":          str(ts),
    }
    await redis_client.xadd("telemetry:alerts", alert)
    await redis_client.publish("alerts", json.dumps(alert))
    log.warning(f"🚨 {alert_type.upper()} | {identity} | {message}")


async def connect_room() -> rtc.Room:
    token = (
        api.AccessToken(API_KEY, API_SECRET)
        .with_identity("telemetry-agent")
        .with_name("Telemetry Agent")
        .with_grants(api.VideoGrants(
            room_join=True, room=ROOM_NAME,
            can_subscribe=True, can_publish=False,
        ))
        .to_jwt()
    )

    room = rtc.Room()

    @room.on("data_received")
    def _on_data(packet: rtc.DataPacket):
        asyncio.create_task(on_data_received(packet))

    @room.on("participant_connected")
    def _on_join(p: rtc.RemoteParticipant):
        log.info(f"👤 Connecte: {p.identity}")

    @room.on("participant_disconnected")
    def _on_left(p: rtc.RemoteParticipant):
        log.info(f"👤 Deconnecte: {p.identity}")
        _participant_counts.pop(p.identity, None)

    await room.connect(LIVEKIT_URL, token)
    log.info(f"✅ Agent connecte a '{ROOM_NAME}'")
    return room


async def main():
    await connect_redis()
    await register_session(ROOM_NAME)

    backoff = 1
    while True:
        room = None
        try:
            room = await connect_room()
            backoff = 1
            disconnected = asyncio.Event()

            @room.on("disconnected")
            def _on_disc(reason):
                log.warning(f"Deconnecte: {reason}")
                disconnected.set()

            await disconnected.wait()
        except Exception as e:
            log.error(f"Erreur connexion: {e}")
        finally:
            if room:
                try: await room.disconnect()
                except Exception: pass

        log.info(f"Reconnexion dans {backoff}s...")
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 30)


if __name__ == "__main__":
    asyncio.run(main())
