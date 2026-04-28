"""
Sport Simulator — Videolog POC
Simule deux participants (coach + trainee) publiant de la telemetrie sportive.

Champs telemetrie:
  gps       — coordonnees GPS {lat, lng, altitude}
  speed     — vitesse en km/h
  heart_rate — frequence cardiaque en bpm
  cadence   — cadence en rpm (pedalage/course)
  role      — "coach" | "trainee"

Scenarios disponibles via SCENARIO env var:
  run       — sortie course a pied (defaut)
  cycling   — sortie velo
  interval  — entrainement fractionne (montees/descentes intenses)
"""
import asyncio, json, os, time, math, random, logging

from livekit import rtc, api

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [SIMULATOR] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

LIVEKIT_URL        = os.environ["LIVEKIT_URL"]
API_KEY            = os.environ["LIVEKIT_API_KEY"]
API_SECRET         = os.environ["LIVEKIT_API_SECRET"]
ROOM_NAME          = os.environ.get("ROOM_NAME", "poc-room")
TELEMETRY_INTERVAL = float(os.environ.get("TELEMETRY_INTERVAL", "1.0"))
SCENARIO           = os.environ.get("SCENARIO", "run")
PARTICIPANT_ID     = os.environ.get("PARTICIPANT_ID", "trainee-01")
ROLE               = os.environ.get("ROLE", "trainee")  # "coach" | "trainee"

# Parcours GPS simule — boucle sur le lac de Tunis
BASE_LAT = 36.8320
BASE_LNG = 10.2060
TRACK_RADIUS_LAT = 0.008   # ~890m
TRACK_RADIUS_LNG = 0.013   # ~1100m


def build_payload(t: float, speed: float, heart_rate: int,
                  cadence: int, role: str) -> dict:
    """Construit le payload telemetrie commun."""
    # Progression sur le circuit (vitesse → position angulaire)
    angle  = (t * speed / 3600 * 1000) / (math.pi * 1000)  # pseudo-distance
    lat    = BASE_LAT + TRACK_RADIUS_LAT * math.sin(angle)
    lng    = BASE_LNG + TRACK_RADIUS_LNG * math.cos(angle)
    alt    = 5.0 + 3.0 * math.sin(angle * 2)  # légères variations d'altitude
    return {
        "type": "telemetry",
        "ts":   time.time() * 1000,
        "payload": {
            "role":       role,
            "speed":      round(max(0.0, speed), 1),          # km/h
            "heart_rate": max(50, min(220, heart_rate)),       # bpm
            "cadence":    max(0, cadence),                     # rpm
            "gps": {
                "lat":      round(lat, 6),
                "lng":      round(lng, 6),
                "altitude": round(alt, 1),
            },
        },
    }


# ── Scenarios ─────────────────────────────────────────────────────────────────

def scenario_run(t: float, role: str) -> dict:
    """Course a pied — allure reguliere avec variations naturelles."""
    if role == "coach":
        # Le coach court a un rythme stable (reference)
        speed      = 12.0 + 0.5 * math.sin(t * 0.05) + random.uniform(-0.2, 0.2)
        heart_rate = int(145 + 3 * math.sin(t * 0.05) + random.gauss(0, 1))
        cadence    = int(168 + 2 * math.sin(t * 0.1) + random.gauss(0, 1))
    else:
        # Le trainee a une allure plus variable, effort perceptible
        speed      = 10.5 + 1.5 * math.sin(t * 0.08) + random.uniform(-0.5, 0.5)
        heart_rate = int(162 + 8 * math.sin(t * 0.06) + random.gauss(0, 2))
        cadence    = int(155 + 5 * math.sin(t * 0.1) + random.gauss(0, 2))
    return build_payload(t, speed, heart_rate, cadence, role)


def scenario_cycling(t: float, role: str) -> dict:
    """Velo — cadence plus elevee, vitesses plus importantes."""
    # Montees / descentes simulees
    slope = math.sin(t * 0.04)
    if role == "coach":
        speed      = 28 + 8 * slope + random.uniform(-1, 1)
        heart_rate = int(138 + 10 * slope + random.gauss(0, 1))
        cadence    = int(85 - 15 * slope + random.gauss(0, 2))
    else:
        speed      = 24 + 10 * slope + random.uniform(-2, 2)
        heart_rate = int(155 + 15 * slope + random.gauss(0, 2))
        cadence    = int(78 - 10 * slope + random.gauss(0, 3))
    return build_payload(t, speed, heart_rate, cadence, role)


def scenario_interval(t: float, role: str) -> dict:
    """Fractionne — phases intenses (30s) + recuperation (60s)."""
    cycle  = t % 90          # cycle de 90s
    effort = cycle < 30      # 30s d'effort, 60s de recuperation

    if role == "coach":
        if effort:
            speed, heart_rate, cadence = (16 + random.uniform(-0.5,0.5),
                                          int(172 + random.gauss(0,1)),
                                          int(180 + random.gauss(0,2)))
        else:
            speed, heart_rate, cadence = (8 + random.uniform(-0.3,0.3),
                                          int(128 + random.gauss(0,2)),
                                          int(150 + random.gauss(0,2)))
    else:
        if effort:
            speed, heart_rate, cadence = (13 + random.uniform(-1,1),
                                          int(181 + random.gauss(0,2)),
                                          int(172 + random.gauss(0,3)))
        else:
            speed, heart_rate, cadence = (6 + random.uniform(-0.5,0.5),
                                          int(135 + random.gauss(0,3)),
                                          int(142 + random.gauss(0,3)))
    return build_payload(t, speed, heart_rate, cadence, role)


SCENARIOS = {
    "run":      scenario_run,
    "cycling":  scenario_cycling,
    "interval": scenario_interval,
}


# ── Publication telemetrie ────────────────────────────────────────────────────

async def publish_telemetry(room: rtc.Room):
    scenario_fn = SCENARIOS.get(SCENARIO, scenario_run)
    log.info(f"📡 {ROLE} | scenario={SCENARIO} | interval={TELEMETRY_INTERVAL}s")
    t, count = 0.0, 0

    while True:
        data    = scenario_fn(t, ROLE)
        payload = json.dumps(data).encode("utf-8")

        try:
            await room.local_participant.publish_data(
                payload,
                reliable=True,
                topic="telemetry",
            )
            count += 1
            if count % 10 == 0:
                p = data["payload"]
                log.info(f"📡 [{ROLE}] #{count} "
                         f"speed={p['speed']}km/h "
                         f"hr={p['heart_rate']}bpm "
                         f"cadence={p['cadence']}rpm")
        except Exception as e:
            log.warning(f"DataTrack error: {e}")

        t += TELEMETRY_INTERVAL
        await asyncio.sleep(TELEMETRY_INTERVAL)


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    await asyncio.sleep(3)

    token = (
        api.AccessToken(API_KEY, API_SECRET)
        .with_identity(PARTICIPANT_ID)
        .with_name(f"{ROLE.capitalize()} ({SCENARIO})")
        .with_grants(api.VideoGrants(
            room_join=True, room=ROOM_NAME,
            can_publish=True, can_subscribe=True,
        ))
        .to_jwt()
    )

    room = rtc.Room()

    @room.on("connected")
    def _on_connected():
        log.info(f"✅ {ROLE} connecte | room={ROOM_NAME}")

    @room.on("disconnected")
    def _on_disconnected(reason):
        log.warning(f"🔌 Deconnecte: {reason}")

    await room.connect(LIVEKIT_URL, token)
    await publish_telemetry(room)


if __name__ == "__main__":
    asyncio.run(main())
