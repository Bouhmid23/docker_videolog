"""Export de séances structurées vers .fit (Garmin), .zwo (Zwift) et .erg."""
from __future__ import annotations

from datetime import UTC, datetime
from html import escape
from typing import Any

from garmin_fit_sdk import Encoder, Profile

from services.workout_estimator import flatten_steps

FIT_SPORT_ENUM = {
    "cyclisme": "cycling",
    "home_trainer": "cycling",
    "course à pied": "running",
    "tapis_course": "running",
    "natation": "swimming",
    "triathlon": "generic",
    "aviron": "rowing",
    "aviron_indoor": "rowing",
    "skierg": "rowing",
    "musculation": "training",
    "cross_training": "training",
    "yoga_pilates": "training",
    "boxe_combat": "boxing",
    "escalade": "rock_climbing",
}


def export_workout_fit(
    title: str,
    sport: str,
    steps: list[dict[str, Any]],
    *,
    ftp_w: int | None = None,
    lthr_bpm: int | None = None,
) -> bytes:
    flat = flatten_steps(steps)
    enc = Encoder()
    enc.on_mesg(
        Profile["mesg_num"]["FILE_ID"],
        {
            "type": "workout",
            "manufacturer": "development",
            "product": 1,
            "time_created": datetime.now(UTC),
        },
    )
    enc.on_mesg(
        Profile["mesg_num"]["WORKOUT"],
        {
            "sport": FIT_SPORT_ENUM.get(sport, "generic"),
            "wkt_name": (title or "Videolog Workout")[:32],
            "num_valid_steps": len(flat),
        },
    )

    for idx, step in enumerate(flat):
        kind = step.get("kind", "active")
        intensity = {
            "warmup": "warmup",
            "recovery": "recovery",
            "rest": "rest",
            "cooldown": "cooldown",
        }.get(kind, "active")

        msg: dict[str, Any] = {
            "message_index": idx,
            "intensity": intensity,
        }
        if step.get("duration_s"):
            msg["duration_type"] = "time"
            msg["duration_value"] = int(step["duration_s"]) * 1000  # ms
        elif step.get("distance_m"):
            msg["duration_type"] = "distance"
            msg["duration_value"] = int(step["distance_m"]) * 100  # cm
        else:
            msg["duration_type"] = "open"
            msg["duration_value"] = 0

        t_type = step.get("target_type", "open")
        lo = step.get("target_min")
        hi = step.get("target_max")
        if t_type == "ftp_pct" and lo is not None:
            msg["target_type"] = "power"
            msg["target_value"] = 0
            if ftp_w and ftp_w > 0:
                # Garmin FIT custom power en Watts + 1000 offset
                msg["custom_target_value_low"] = round(ftp_w * (float(lo) / 100.0)) + 1000
                msg["custom_target_value_high"] = (
                    round(ftp_w * (float(hi if hi is not None else lo) / 100.0)) + 1000
                )
            else:
                msg["custom_target_value_low"] = round(float(lo))
                msg["custom_target_value_high"] = round(float(hi if hi is not None else lo))
        elif t_type == "lthr_pct" and lo is not None:
            msg["target_type"] = "heart_rate"
            msg["target_value"] = 0
            if lthr_bpm and lthr_bpm > 0:
                msg["custom_target_value_low"] = round(lthr_bpm * (float(lo) / 100.0)) + 100
                msg["custom_target_value_high"] = (
                    round(lthr_bpm * (float(hi if hi is not None else lo) / 100.0)) + 100
                )
            else:
                msg["custom_target_value_low"] = round(float(lo))
                msg["custom_target_value_high"] = round(float(hi if hi is not None else lo))
        else:
            msg["target_type"] = "open"
            msg["target_value"] = 0

        enc.on_mesg(Profile["mesg_num"]["WORKOUT_STEP"], msg)

    return bytes(enc.close())


def export_workout_zwo(
    title: str,
    description: str | None,
    steps: list[dict[str, Any]],
    *,
    sport: str = "cyclisme",
) -> str:
    flat = flatten_steps(steps)
    zwo_sport = "run" if sport in ("course à pied", "tapis_course") else "bike"
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<workout_file>",
        "  <author>Videolog</author>",
        f"  <name>{escape(title or 'Videolog Workout')}</name>",
        f"  <description>{escape(description or '')}</description>",
        f"  <sportType>{zwo_sport}</sportType>",
        "  <workout>",
    ]
    for step in flat:
        dur = int(step.get("duration_s") or 300)
        lo = float(step.get("target_min") or 65) / 100.0
        hi = float(step.get("target_max") or step.get("target_min") or 65) / 100.0
        kind = step.get("kind", "active")
        if kind == "warmup":
            lines.append(
                f'    <Warmup Duration="{dur}" PowerLow="{lo:.2f}" PowerHigh="{hi:.2f}"/>'
            )
        elif kind == "cooldown":
            lines.append(
                f'    <Cooldown Duration="{dur}" PowerLow="{hi:.2f}" PowerHigh="{lo:.2f}"/>'
            )
        else:
            power = (lo + hi) / 2.0
            lines.append(f'    <SteadyState Duration="{dur}" Power="{power:.2f}"/>')

    lines.extend(["  </workout>", "</workout_file>"])
    return "\n".join(lines)


def export_workout_erg(
    title: str,
    steps: list[dict[str, Any]],
    *,
    ftp_w: int | None = None,
) -> str:
    flat = flatten_steps(steps)
    ref_ftp = int(ftp_w or 250)
    lines = [
        "[COURSE HEADER]",
        "VERSION = 2",
        "UNITS = ENGLISH",
        f"DESCRIPTION = {title or 'Videolog Workout'}",
        f"FTP = {ref_ftp}",
        "MINUTES WATTS",
        "[END COURSE HEADER]",
        "[COURSE DATA]",
    ]
    elapsed_min = 0.0
    for step in flat:
        dur_min = float(step.get("duration_s") or 300) / 60.0
        lo = float(step.get("target_min") or 65)
        hi = float(step.get("target_max") or lo)
        pct = (lo + hi) / 2.0
        watts = round(ref_ftp * (pct / 100.0))
        lines.append(f"{elapsed_min:.2f}\t{watts}")
        elapsed_min += dur_min
        lines.append(f"{elapsed_min:.2f}\t{watts}")
    lines.append("[END COURSE DATA]")
    return "\n".join(lines)
