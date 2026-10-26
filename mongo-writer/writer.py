"""
MongoDB Writer — Videolog POC
Consomme Redis Streams → insère en batch dans MongoDB.
Inclut: alerts stream, dead letter queue, session sync.
"""
import asyncio, json, os, logging
from datetime import datetime, UTC

import redis.asyncio as aioredis
from motor.motor_asyncio import AsyncIOMotorClient

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [WRITER] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

MONGO_URL      = os.environ["MONGO_URL"]
REDIS_URL      = os.environ["REDIS_URL"]
BATCH_SIZE     = int(os.environ.get("BATCH_SIZE", "50"))
FLUSH_INTERVAL = float(os.environ.get("FLUSH_INTERVAL", "0.5"))
STREAM_KEY     = "telemetry:stream"
ALERTS_STREAM  = "telemetry:alerts"
CONSUMER_GROUP = "mongo-writers"
CONSUMER_NAME  = "writer-1"
DLQ_TIMEOUT_MS = 30_000
DLQ_STREAM     = "telemetry:dlq"


def without_posture(value):
    """Remove posture keys at every nesting level before persisting telemetry."""
    if isinstance(value, dict):
        return {key: without_posture(item) for key, item in value.items()
                if str(key).casefold() != "posture"}
    if isinstance(value, list):
        return [without_posture(item) for item in value]
    return value


def number_or_none(value, cast=float, default=0):
    if value is None or value == "":
        return None
    try:
        return cast(value)
    except (TypeError, ValueError, OverflowError):
        return default


async def ensure_groups(r: aioredis.Redis):
    for stream in (STREAM_KEY, ALERTS_STREAM):
        try:
            await r.xgroup_create(stream, CONSUMER_GROUP, id="0", mkstream=True)
            log.info(f"📌 Group '{CONSUMER_GROUP}' créé sur {stream}")
        except Exception as e:
            if "BUSYGROUP" not in str(e): raise


def parse_telemetry(msg_id: str, data: dict) -> dict:
    payload = {}
    try: payload = json.loads(data.get("raw", "{}"))
    except json.JSONDecodeError: pass
    payload = without_posture(payload)
    document = {
        "_id_redis":   msg_id,
        "room_name":   data.get("room_name", "unknown"),
        "participant": data.get("participant", "unknown"),
        "ts":          number_or_none(data.get("ts", 0)),
        "capture_ts":  number_or_none(data.get("capture_ts", data.get("ts", 0))),
        "pts_ms":      number_or_none(data.get("pts_ms", 0)),
        "server_ts":   number_or_none(data.get("server_ts", 0)),
        "telemetry": {
            "role":        data.get("role", "trainee"),
            "speed":       number_or_none(data.get("speed", 0)),
            "heart_rate":  number_or_none(data.get("heart_rate", 0), int),
            "cadence":     number_or_none(data.get("cadence", 0), int),
            "gps": {
                "lat":      number_or_none(data.get("lat", 0)),
                "lng":      number_or_none(data.get("lng", 0)),
                "altitude": number_or_none(data.get("altitude", 0)),
            },
            **payload,
        },
        "inserted_at": datetime.now(UTC),
    }
    if data.get("packet_b64"):
        document["packet_b64"] = data["packet_b64"]
        document["packet_encoding"] = data.get("packet_encoding", "base64")
        document["telemetry_decoded"] = data.get("telemetry_decoded") == "true"
    return document


def parse_alert(msg_id: str, data: dict) -> dict:
    return {
        "_id_redis":   msg_id,
        "room_name":   data.get("room_name", "unknown"),
        "participant": data.get("participant", "unknown"),
        "type":        data.get("type", "unknown"),
        "value":       float(data.get("value", 0)),
        "message":     data.get("message", ""),
        "pts_ms":      float(data.get("pts_ms", 0)),
        "ts":          float(data.get("ts", 0)),
        "inserted_at": datetime.now(UTC),
    }


async def reclaim_pending(r: aioredis.Redis, col):
    """Récupère les messages PEL non ACK depuis > DLQ_TIMEOUT_MS."""
    try:
        result = await r.xautoclaim(
            STREAM_KEY, CONSUMER_GROUP, CONSUMER_NAME,
            min_idle_time=DLQ_TIMEOUT_MS, start_id="0-0", count=10,
        )
        # xautoclaim → (next_id, [(id, fields), ...], [deleted_ids])
        messages = result[1] if len(result) > 1 else []
        if not messages: return

        log.warning(f"♻️  {len(messages)} messages PEL réclamés")
        buf, acks = [], []
        for msg_id, data in messages:
            try:
                buf.append(parse_telemetry(msg_id, data))
                acks.append(msg_id)
            except Exception as e:
                await r.xadd(DLQ_STREAM, {"msg_id": msg_id, "error": str(e)},
                             maxlen=10000, approximate=True)
                await r.xack(STREAM_KEY, CONSUMER_GROUP, msg_id)
                log.error(f"💀 DLQ: {msg_id} — {e}")
        if buf:
            await col.insert_many(buf, ordered=False)
            await r.xack(STREAM_KEY, CONSUMER_GROUP, *acks)
    except Exception as e:
        log.debug(f"xautoclaim: {e}")


async def writer():
    r        = aioredis.from_url(REDIS_URL, decode_responses=True)
    mongo    = AsyncIOMotorClient(MONGO_URL)
    col      = mongo.videolog.telemetry
    alerts   = mongo.videolog.alerts
    sessions = mongo.videolog.sessions

    await r.ping(); log.info("✅ Redis OK")
    await mongo.admin.command("ping"); log.info("✅ MongoDB OK")
    await ensure_groups(r)

    # Index TTL 7 jours
    try:
        await col.create_index("inserted_at", expireAfterSeconds=7*24*3600)
        await col.create_index([("room_name", 1), ("server_ts", 1)])
        await alerts.create_index("inserted_at", expireAfterSeconds=7*24*3600)
    except Exception: pass

    try:
        await col.create_index([
            ("room_name", 1), ("participant", 1), ("server_ts", -1),
            ("_id_redis", -1),
        ])
    except Exception:
        log.exception("Failed to create telemetry history index")

    buf_tel, buf_alt = [], []
    acks_tel, acks_alt = [], []
    total = 0
    reclaim_counter = 0
    session_sync_at = 0.0

    log.info(f"🚀 Writer | batch={BATCH_SIZE} flush={FLUSH_INTERVAL}s")

    while True:
        try:
            # Lire télémétrie + alertes en même appel
            results = await r.xreadgroup(
                CONSUMER_GROUP, CONSUMER_NAME,
                {STREAM_KEY: ">", ALERTS_STREAM: ">"},
                count=BATCH_SIZE,
                block=int(FLUSH_INTERVAL * 1000),
            )

            if results:
                for stream_name, messages in results:
                    for msg_id, data in messages:
                        if stream_name == STREAM_KEY:
                            buf_tel.append(parse_telemetry(msg_id, data))
                            acks_tel.append(msg_id)
                        else:
                            buf_alt.append(parse_alert(msg_id, data))
                            acks_alt.append(msg_id)

            # Flush télémétrie
            if buf_tel:
                await col.insert_many(buf_tel, ordered=False)
                await r.xack(STREAM_KEY, CONSUMER_GROUP, *acks_tel)
                total += len(buf_tel)
                log.info(f"💾 +{len(buf_tel)} telemetry | total={total} "
                         f"pts={buf_tel[-1]['pts_ms']:.0f}ms")
                buf_tel.clear(); acks_tel.clear()

            # Flush alertes
            if buf_alt:
                await alerts.insert_many(buf_alt, ordered=False)
                await r.xack(ALERTS_STREAM, CONSUMER_GROUP, *acks_alt)
                log.warning(f"🚨 {len(buf_alt)} alertes persistées")
                buf_alt.clear(); acks_alt.clear()

            # Reclaim PEL toutes les 100 itérations
            reclaim_counter += 1
            if reclaim_counter >= 100:
                await reclaim_pending(r, col)
                reclaim_counter = 0

            # Sync sessions toutes les 30s
            now = asyncio.get_event_loop().time()
            if now - session_sync_at > 30:
                try:
                    keys = await r.keys("session:*")
                    for key in keys:
                        d = await r.hgetall(key)
                        if d:
                            await sessions.update_one(
                                {"room_name": d["room_name"]},
                                {"$set": {**d, "updated_at": datetime.now(UTC)}},
                                upsert=True,
                            )
                except Exception as e:
                    log.debug(f"sync_sessions: {e}")
                session_sync_at = now

        except Exception as e:
            log.error(f"Writer error: {e}")
            await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(writer())
