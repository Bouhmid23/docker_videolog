"""Tests unitaires Phase 2 : estimation séances, export .fit/.zwo/.erg et calcul de conformité."""
import io

from garmin_fit_sdk import Decoder, Stream

from services.workout_estimator import estimate_workout
from services.workout_export import (
    export_workout_erg,
    export_workout_fit,
    export_workout_zwo,
)
from services.workout_matching import compute_compliance


def test_estimate_workout_with_repeats():
    steps = [
        {"kind": "warmup", "duration_s": 600, "target_type": "ftp_pct", "target_min": 55, "target_max": 65},
        {
            "kind": "repeat",
            "repeat_count": 3,
            "steps": [
                {"kind": "active", "duration_s": 300, "target_type": "ftp_pct", "target_min": 100, "target_max": 100},
                {"kind": "recovery", "duration_s": 180, "target_type": "ftp_pct", "target_min": 50, "target_max": 50},
            ],
        },
        {"kind": "cooldown", "duration_s": 300, "target_type": "ftp_pct", "target_min": 50, "target_max": 50},
    ]
    est = estimate_workout(steps)
    # 600 + 3*(300+180) + 300 = 2340 s
    assert est["estimated_duration_s"] == 2340
    assert len(est["blocks"]) == 8
    # 15 min @ 1.0 IF = 25 TSS, plus warmup/recovery/cooldown (~11.5) => ~36.5
    assert 34.0 <= est["estimated_tss"] <= 40.0


def test_export_fit_zwo_erg_roundtrip():
    steps = [
        {"kind": "warmup", "duration_s": 300, "target_type": "ftp_pct", "target_min": 55, "target_max": 65},
        {"kind": "active", "duration_s": 600, "target_type": "ftp_pct", "target_min": 95, "target_max": 105},
    ]
    fit_bytes = export_workout_fit("Seuil 10min", "cyclisme", steps, ftp_w=260)
    decoder = Decoder(Stream.from_bytes_io(io.BytesIO(fit_bytes)))
    assert decoder.is_fit()
    msgs, errors = decoder.read()
    assert not errors
    assert len(msgs.get("workout_mesgs") or []) == 1
    assert len(msgs.get("workout_step_mesgs") or []) == 2

    zwo = export_workout_zwo("Seuil 10min", "Test", steps)
    assert "<workout_file>" in zwo
    assert "<Warmup" in zwo
    assert "<SteadyState" in zwo

    erg = export_workout_erg("Seuil 10min", steps, ftp_w=260)
    assert "[COURSE HEADER]" in erg
    assert "FTP = 260" in erg


def test_compliance_scoring():
    planned = {"estimated_duration_s": 3600, "estimated_tss": 80.0}
    green = compute_compliance(planned, {"duration_s": 3500, "metrics": {"tss": 78.0}})
    assert green["color"] == "green"
    assert green["score"] >= 95

    orange = compute_compliance(planned, {"duration_s": 2200, "metrics": {"tss": 50.0}})
    assert orange["color"] == "orange"

    red = compute_compliance(planned, {"duration_s": 900, "metrics": {"tss": 20.0}})
    assert red["color"] == "red"
