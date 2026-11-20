"""Ingestion des fichiers d'activité FIT, GPX et TCX vers un format 1 Hz colonnaire unifié."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import io
import math
from typing import Any
import xml.etree.ElementTree as ET

from garmin_fit_sdk import Decoder, Stream
import gpxpy

SEMICIRCLES_TO_DEG = 180.0 / float(1 << 31)

FIT_SUB_SPORT_MAP = {
    "indoor_cycling": "home_trainer",
    "virtual_activity": "home_trainer",
    "spin": "home_trainer",
    "e_bike_indoor": "home_trainer",
    "treadmill": "tapis_course",
    "indoor_running": "tapis_course",
    "indoor_rowing": "aviron_indoor",
    "indoor_skiing": "skierg",
    "strength_training": "musculation",
    "hiit": "cross_training",
    "cardio_training": "cross_training",
    "circuit_training": "cross_training",
    "bootcamp": "cross_training",
    "yoga": "yoga_pilates",
    "pilates": "yoga_pilates",
    "stretching": "yoga_pilates",
    "flexibility_training": "yoga_pilates",
    "boxing": "boxe_combat",
    "mixed_martial_arts": "boxe_combat",
    "kickboxing": "boxe_combat",
    "indoor_climbing": "escalade",
    "bouldering": "escalade",
    "lap_swimming": "natation",
}

FIT_SPORT_MAP = {
    "cycling": "cyclisme",
    "running": "course à pied",
    "swimming": "natation",
    "rowing": "aviron",
    "soccer": "football",
    "triathlon": "triathlon",
    "multisport": "triathlon",
    "fitness_equipment": "cross_training",
    "training": "cross_training",
    "hiit": "cross_training",
    "rock_climbing": "escalade",
    "mountaineering": "escalade",
    "boxing": "boxe_combat",
}

VALID_SPORTS = (
    "cyclisme",
    "home_trainer",
    "course à pied",
    "tapis_course",
    "triathlon",
    "natation",
    "aviron",
    "aviron_indoor",
    "skierg",
    "musculation",
    "cross_training",
    "yoga_pilates",
    "boxe_combat",
    "escalade",
    "football",
    "rugby",
    "autre",
)


@dataclass
class ParsedActivity:
    sport: str
    start_time: datetime
    duration_s: float
    moving_time_s: float
    distance_m: float
    streams: dict[str, list[Any]]
    laps: list[dict[str, Any]]
    title: str | None = None


def map_sport(raw: str | None, sub_sport: str | None = None) -> str:
    if sub_sport:
        sub_clean = str(sub_sport).strip().lower()
        if sub_clean in VALID_SPORTS:
            return sub_clean
        for key, val in FIT_SUB_SPORT_MAP.items():
            if key in sub_clean:
                return val
    if not raw:
        return "autre"
    cleaned = str(raw).strip().lower()
    if cleaned in VALID_SPORTS:
        return cleaned
    for key, val in FIT_SUB_SPORT_MAP.items():
        if key in cleaned:
            return val
    for key, val in FIT_SPORT_MAP.items():
        if key in cleaned:
            return val
    return "autre"


def compute_dedup_hash(
    uid: str, sport: str, start_time: datetime, duration_s: float
) -> str:
    ts_bucket = int(start_time.timestamp() // 60) * 60
    dur_bucket = int(round(duration_s / 10.0) * 10)
    raw = f"{uid}:{sport}:{ts_bucket}:{dur_bucket}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _resample_1hz(samples: list[dict[str, Any]], start_ts: float) -> dict[str, list[Any]]:
    if not samples:
        return {
            "t": [],
            "hr": [],
            "power": [],
            "cadence": [],
            "speed": [],
            "lat": [],
            "lng": [],
            "alt": [],
        }

    # Indexer par seconde entière relative à start_ts
    by_sec: dict[int, dict[str, Any]] = {}
    max_sec = 0
    for s in samples:
        sec = max(0, int(round(s["ts"] - start_ts)))
        if sec > max_sec:
            max_sec = sec
        by_sec[sec] = s

    # Limiter les trous éventuels à 12h max pour éviter l'explosion mémoire sur horloge corrompue
    max_sec = min(max_sec, 43200)
    t_arr: list[int] = []
    hr_arr: list[int | None] = []
    power_arr: list[float | None] = []
    cad_arr: list[int | None] = []
    speed_arr: list[float | None] = []
    lat_arr: list[float | None] = []
    lng_arr: list[float | None] = []
    alt_arr: list[float | None] = []

    for sec in range(max_sec + 1):
        row = by_sec.get(sec)
        t_arr.append(sec)
        if row is None:
            hr_arr.append(None)
            power_arr.append(None)
            cad_arr.append(None)
            speed_arr.append(None)
            lat_arr.append(None)
            lng_arr.append(None)
            alt_arr.append(None)
        else:
            hr_arr.append(row.get("hr"))
            power_arr.append(row.get("power"))
            cad_arr.append(row.get("cadence"))
            speed_arr.append(row.get("speed"))
            lat_arr.append(row.get("lat"))
            lng_arr.append(row.get("lng"))
            alt_arr.append(row.get("alt"))

    return {
        "t": t_arr,
        "hr": hr_arr,
        "power": power_arr,
        "cadence": cad_arr,
        "speed": speed_arr,
        "lat": lat_arr,
        "lng": lng_arr,
        "alt": alt_arr,
    }


def parse_fit_bytes(data: bytes) -> ParsedActivity:
    stream = Stream.from_bytes_io(io.BytesIO(data))
    decoder = Decoder(stream)
    if not decoder.is_fit():
        raise ValueError("Le fichier fourni n'est pas un fichier FIT valide.")
    messages, errors = decoder.read()
    records = messages.get("record_mesgs") or []
    sessions = messages.get("session_mesgs") or []
    laps_raw = messages.get("lap_mesgs") or []

    if not records and not sessions:
        raise ValueError(f"Aucune donnée exploitable dans le fichier FIT ({errors[:2]}).")

    sess0 = sessions[0] if sessions else {}
    sport = map_sport(sess0.get("sport"), sess0.get("sub_sport"))

    samples: list[dict[str, Any]] = []
    total_dist = 0.0
    prev_lat: float | None = None
    prev_lng: float | None = None

    for rec in records:
        ts_val = rec.get("timestamp")
        if not isinstance(ts_val, datetime):
            continue
        if ts_val.tzinfo is None:
            ts_val = ts_val.replace(tzinfo=UTC)

        lat_sc = rec.get("position_lat")
        lng_sc = rec.get("position_long")
        lat = (
            round(lat_sc * SEMICIRCLES_TO_DEG, 7)
            if isinstance(lat_sc, (int, float))
            else None
        )
        lng = (
            round(lng_sc * SEMICIRCLES_TO_DEG, 7)
            if isinstance(lng_sc, (int, float))
            else None
        )
        if lat is not None and lng is not None and (lat != 0 or lng != 0):
            if prev_lat is not None and prev_lng is not None:
                total_dist += _haversine_m(prev_lat, prev_lng, lat, lng)
            prev_lat, prev_lng = lat, lng
        else:
            lat, lng = None, None

        speed_ms = rec.get("enhanced_speed")
        if speed_ms is None:
            speed_ms = rec.get("speed")
        speed_kmh = (
            round(float(speed_ms) * 3.6, 2)
            if isinstance(speed_ms, (int, float))
            else None
        )

        alt = rec.get("enhanced_altitude")
        if alt is None:
            alt = rec.get("altitude")

        hr = rec.get("heart_rate")
        power = rec.get("power")
        cad = rec.get("cadence")

        samples.append(
            {
                "ts": ts_val.timestamp(),
                "hr": int(hr) if isinstance(hr, (int, float)) else None,
                "power": float(power) if isinstance(power, (int, float)) else None,
                "cadence": int(cad) if isinstance(cad, (int, float)) else None,
                "speed": speed_kmh,
                "lat": lat,
                "lng": lng,
                "alt": round(float(alt), 2) if isinstance(alt, (int, float)) else None,
            }
        )

    start_dt = sess0.get("start_time")
    if not isinstance(start_dt, datetime):
        if samples:
            start_dt = datetime.fromtimestamp(samples[0]["ts"], UTC)
        else:
            start_dt = datetime.now(UTC)
    elif start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=UTC)

    streams = _resample_1hz(samples, start_dt.timestamp())
    duration_s = float(
        sess0.get("total_elapsed_time")
        or (len(streams["t"]) - 1 if len(streams["t"]) > 1 else 0)
    )
    moving_s = float(sess0.get("total_timer_time") or duration_s)
    distance_m = float(sess0.get("total_distance") or total_dist)

    laps: list[dict[str, Any]] = []
    for idx, lap in enumerate(laps_raw, start=1):
        laps.append(
            {
                "index": idx,
                "duration_s": float(lap.get("total_elapsed_time") or 0),
                "distance_m": float(lap.get("total_distance") or 0),
                "avg_power": lap.get("avg_power"),
                "avg_hr": lap.get("avg_heart_rate"),
                "avg_cadence": lap.get("avg_cadence"),
            }
        )

    return ParsedActivity(
        sport=sport,
        start_time=start_dt,
        duration_s=duration_s,
        moving_time_s=moving_s,
        distance_m=distance_m,
        streams=streams,
        laps=laps,
    )


def parse_gpx_bytes(data: bytes) -> ParsedActivity:
    try:
        gpx = gpxpy.parse(data.decode("utf-8", errors="replace"))
    except Exception as exc:
        raise ValueError(f"Fichier GPX invalide : {exc}") from exc

    samples: list[dict[str, Any]] = []
    total_dist = 0.0
    prev_pt: tuple[float, float, float] | None = None
    track_name = None

    for track in gpx.tracks:
        if not track_name and track.name:
            track_name = track.name
        for seg in track.segments:
            for pt in seg.points:
                if pt.time is None:
                    continue
                dt = pt.time if pt.time.tzinfo else pt.time.replace(tzinfo=UTC)
                ts = dt.timestamp()
                speed_kmh = None
                if prev_pt is not None:
                    d_m = _haversine_m(prev_pt[0], prev_pt[1], pt.latitude, pt.longitude)
                    total_dist += d_m
                    dt_s = ts - prev_pt[2]
                    if dt_s > 0:
                        speed_kmh = round((d_m / dt_s) * 3.6, 2)
                prev_pt = (pt.latitude, pt.longitude, ts)
                samples.append(
                    {
                        "ts": ts,
                        "hr": None,
                        "power": None,
                        "cadence": None,
                        "speed": speed_kmh,
                        "lat": round(pt.latitude, 7),
                        "lng": round(pt.longitude, 7),
                        "alt": round(pt.elevation, 2)
                        if pt.elevation is not None
                        else None,
                    }
                )

    if not samples:
        raise ValueError("Aucun point GPS horodaté dans le fichier GPX.")

    start_dt = datetime.fromtimestamp(samples[0]["ts"], UTC)
    streams = _resample_1hz(samples, start_dt.timestamp())
    duration_s = max(0.0, samples[-1]["ts"] - samples[0]["ts"])
    return ParsedActivity(
        sport="autre",
        start_time=start_dt,
        duration_s=duration_s,
        moving_time_s=duration_s,
        distance_m=round(total_dist, 1),
        streams=streams,
        laps=[],
        title=track_name,
    )


def parse_tcx_bytes(data: bytes) -> ParsedActivity:
    try:
        root = ET.fromstring(data.decode("utf-8", errors="replace"))
    except ET.ParseError as exc:
        raise ValueError(f"Fichier TCX invalide : {exc}") from exc

    def _local(tag: str) -> str:
        return tag.split("}", 1)[-1]

    sport = "autre"
    samples: list[dict[str, Any]] = []
    total_dist = 0.0
    prev_pos: tuple[float, float, float] | None = None

    for act in root.iter():
        if _local(act.tag) == "Activity":
            sport = map_sport(act.attrib.get("Sport"))
            break

    for tp in root.iter():
        if _local(tp.tag) != "Trackpoint":
            continue
        ts_val: float | None = None
        lat = lng = alt = hr = cad = power = dist_m = None
        for child in tp:
            name = _local(child.tag)
            if name == "Time" and child.text:
                dt = datetime.fromisoformat(child.text.strip().replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                ts_val = dt.timestamp()
            elif name == "AltitudeMeters" and child.text:
                alt = float(child.text)
            elif name == "DistanceMeters" and child.text:
                dist_m = float(child.text)
            elif name == "Cadence" and child.text:
                cad = int(float(child.text))
            elif name == "HeartRateBpm":
                for sub in child:
                    if _local(sub.tag) == "Value" and sub.text:
                        hr = int(float(sub.text))
            elif name == "Position":
                for sub in child:
                    if _local(sub.tag) == "LatitudeDegrees" and sub.text:
                        lat = round(float(sub.text), 7)
                    elif _local(sub.tag) == "LongitudeDegrees" and sub.text:
                        lng = round(float(sub.text), 7)
            elif name == "Extensions":
                for sub in child.iter():
                    if _local(sub.tag) == "Watts" and sub.text:
                        power = float(sub.text)

        if ts_val is None:
            continue
        speed_kmh = None
        if lat is not None and lng is not None:
            if prev_pos is not None:
                d_m = _haversine_m(prev_pos[0], prev_pos[1], lat, lng)
                total_dist += d_m
                dt_s = ts_val - prev_pos[2]
                if dt_s > 0:
                    speed_kmh = round((d_m / dt_s) * 3.6, 2)
            prev_pos = (lat, lng, ts_val)
        if dist_m is not None and dist_m > total_dist:
            total_dist = dist_m

        samples.append(
            {
                "ts": ts_val,
                "hr": hr,
                "power": power,
                "cadence": cad,
                "speed": speed_kmh,
                "lat": lat,
                "lng": lng,
                "alt": round(alt, 2) if alt is not None else None,
            }
        )

    if not samples:
        raise ValueError("Aucun trackpoint horodaté dans le fichier TCX.")

    start_dt = datetime.fromtimestamp(samples[0]["ts"], UTC)
    streams = _resample_1hz(samples, start_dt.timestamp())
    duration_s = max(0.0, samples[-1]["ts"] - samples[0]["ts"])
    return ParsedActivity(
        sport=sport,
        start_time=start_dt,
        duration_s=duration_s,
        moving_time_s=duration_s,
        distance_m=round(total_dist, 1),
        streams=streams,
        laps=[],
    )


def parse_activity_file(filename: str, data: bytes) -> ParsedActivity:
    lower = filename.lower()
    if lower.endswith(".fit"):
        return parse_fit_bytes(data)
    if lower.endswith(".gpx"):
        return parse_gpx_bytes(data)
    if lower.endswith(".tcx"):
        return parse_tcx_bytes(data)
    raise ValueError("Format non supporté. Formats acceptés : .fit, .gpx, .tcx.")
