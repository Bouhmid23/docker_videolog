"""Tests unitaires Phase 1 : métriques d'endurance (NP, IF, TSS, rTSS, TRIMP, mean-max) et parsing FIT/GPX/TCX."""
from datetime import UTC, datetime, timedelta

from garmin_fit_sdk import Encoder, Profile
import pytest

from services.ingestion import (
    compute_dedup_hash,
    parse_activity_file,
    parse_fit_bytes,
    parse_gpx_bytes,
    parse_tcx_bytes,
)
from services.metrics import compute_activity_metrics


def test_power_metrics_np_if_tss_zones_mean_max():
    # 30 min à 200 W puis 30 min à 300 W, FTP = 250 W
    power = [200.0] * 1800 + [300.0] * 1800
    hr = [140] * 1800 + [170] * 1800
    streams = {
        "t": list(range(3600)),
        "power": power,
        "hr": hr,
        "speed": [32.0] * 3600,
        "cadence": [90] * 3600,
        "alt": [100.0 + (i * 0.01) for i in range(3600)],
    }
    thresholds = {
        "ftp_w": 250,
        "lthr_bpm": 168,
        "hr_max": 190,
        "hr_rest": 50,
    }
    m = compute_activity_metrics(
        streams,
        duration_s=3600.0,
        distance_m=32000.0,
        sport="cyclisme",
        thresholds=thresholds,
    )

    assert m["avg_power"] == 250.0
    assert m["max_power"] == 300.0
    # ((200^4 + 300^4)/2)^0.25 = 263.90...
    assert 262.5 <= m["normalized_power"] <= 265.0
    assert 1.04 <= m["intensity_factor"] <= 1.07
    assert 109.0 <= m["tss"] <= 114.0
    assert m["tss_method"] == "power"
    assert m["trimp"] > 0
    assert m["hr_tss"] > 0
    assert m["mean_max_power"]["1800"] == 300.0
    assert m["mean_max_power"]["3600"] == 250.0
    assert m["time_in_power_zones"]["Z3"] > 1700
    assert m["time_in_power_zones"]["Z6"] > 1700
    assert m["elevation_gain_m"] > 30.0


def test_running_rtss_and_hr_fallback():
    # 1h à 4:00/km (240 s/km) avec allure seuil 4:00/km => rTSS = 100
    streams = {
        "t": list(range(3600)),
        "power": [None] * 3600,
        "hr": [162] * 3600,
        "speed": [15.0] * 3600,
        "cadence": [88] * 3600,
        "alt": [50.0] * 3600,
    }
    m_run = compute_activity_metrics(
        streams,
        duration_s=3600.0,
        distance_m=15000.0,
        sport="course à pied",
        thresholds={"threshold_pace_s_per_km": 240, "lthr_bpm": 165, "hr_max": 190, "hr_rest": 50},
    )
    assert m_run["tss_method"] == "pace"
    assert 99.0 <= m_run["rtss"] <= 101.0

    # Sans allure seuil ni puissance => fallback hrTSS
    m_hr = compute_activity_metrics(
        streams,
        duration_s=3600.0,
        distance_m=15000.0,
        sport="autre",
        thresholds={"lthr_bpm": 165, "hr_max": 190, "hr_rest": 50},
    )
    assert m_hr["tss_method"] == "hr"
    assert m_hr["tss"] == m_hr["hr_tss"]


def test_parse_fit_bytes_with_sdk_encoder():
    t0 = datetime(2026, 11, 10, 8, 0, 0, tzinfo=UTC)
    enc = Encoder()
    enc.on_mesg(
        Profile["mesg_num"]["FILE_ID"],
        {"type": "activity", "manufacturer": "development", "product": 1, "time_created": t0},
    )
    for i in range(60):
        enc.on_mesg(
            Profile["mesg_num"]["RECORD"],
            {
                "timestamp": t0 + timedelta(seconds=i),
                "heart_rate": 145 + (i % 5),
                "power": 240,
                "cadence": 90,
                "enhanced_speed": 9.5,
                "enhanced_altitude": 120.0 + i * 0.1,
            },
        )
    enc.on_mesg(
        Profile["mesg_num"]["LAP"],
        {
            "timestamp": t0 + timedelta(seconds=59),
            "start_time": t0,
            "total_elapsed_time": 60.0,
            "total_distance": 570.0,
            "avg_power": 240,
            "avg_heart_rate": 147,
        },
    )
    enc.on_mesg(
        Profile["mesg_num"]["SESSION"],
        {
            "timestamp": t0 + timedelta(seconds=59),
            "start_time": t0,
            "sport": "cycling",
            "total_elapsed_time": 60.0,
            "total_timer_time": 60.0,
            "total_distance": 570.0,
        },
    )
    fit_bytes = bytes(enc.close())

    parsed = parse_fit_bytes(fit_bytes)
    assert parsed.sport == "cyclisme"
    assert parsed.duration_s == 60.0
    assert parsed.distance_m == 570.0
    assert len(parsed.laps) == 1
    assert len(parsed.streams["t"]) == 60
    assert parsed.streams["power"][0] == 240.0
    assert parsed.streams["hr"][0] == 145


def test_parse_gpx_and_tcx_bytes():
    gpx = b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test">
  <trk><name>Sortie matin</name><trkseg>
    <trkpt lat="48.8566" lon="2.3522"><ele>35</ele><time>2026-11-10T08:00:00Z</time></trkpt>
    <trkpt lat="48.8576" lon="2.3522"><ele>37</ele><time>2026-11-10T08:00:30Z</time></trkpt>
  </trkseg></trk>
</gpx>"""
    p_gpx = parse_gpx_bytes(gpx)
    assert p_gpx.title == "Sortie matin"
    assert p_gpx.duration_s == 30.0
    assert p_gpx.distance_m > 100.0
    assert len(p_gpx.streams["t"]) == 31

    tcx = b"""<?xml version="1.0" encoding="UTF-8"?>
<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">
  <Activities><Activity Sport="Running"><Id>2026-11-10T08:00:00Z</Id>
    <Lap StartTime="2026-11-10T08:00:00Z"><Track>
      <Trackpoint><Time>2026-11-10T08:00:00Z</Time><HeartRateBpm><Value>150</Value></HeartRateBpm><Cadence>86</Cadence></Trackpoint>
      <Trackpoint><Time>2026-11-10T08:00:10Z</Time><HeartRateBpm><Value>155</Value></HeartRateBpm><Cadence>88</Cadence></Trackpoint>
    </Track></Lap>
  </Activity></Activities>
</TrainingCenterDatabase>"""
    p_tcx = parse_tcx_bytes(tcx)
    assert p_tcx.sport == "course à pied"
    assert p_tcx.duration_s == 10.0
    assert p_tcx.streams["hr"][0] == 150
    assert p_tcx.streams["hr"][10] == 155

    with pytest.raises(ValueError):
        parse_activity_file("bad.csv", b"1,2,3")

    h1 = compute_dedup_hash("u1", "cyclisme", datetime(2026, 11, 10, 8, 0, 5, tzinfo=UTC), 602.0)
    h2 = compute_dedup_hash("u1", "cyclisme", datetime(2026, 11, 10, 8, 0, 45, tzinfo=UTC), 599.0)
    assert h1 == h2
