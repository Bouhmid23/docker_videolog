"""Estimation de la durée, distance, TSS et aperçu aplati des étapes d'une séance structurée."""
from __future__ import annotations

from typing import Any


def _step_intensity_factor(step: dict[str, Any]) -> float:
    t_type = step.get("target_type", "open")
    lo = step.get("target_min")
    hi = step.get("target_max")
    vals = [float(v) for v in (lo, hi) if v is not None]
    mid = sum(vals) / len(vals) if vals else None

    if t_type in ("ftp_pct", "lthr_pct", "pace_pct") and mid is not None:
        return max(0.2, min(2.5, mid / 100.0))
    if t_type == "rpe" and mid is not None:
        # Approximation RPE 1..10 -> IF 0.45..1.15
        return max(0.35, min(1.3, 0.35 + mid * 0.08))

    kind = step.get("kind", "active")
    return {
        "warmup": 0.60,
        "recovery": 0.55,
        "rest": 0.45,
        "cooldown": 0.55,
        "active": 0.85,
    }.get(kind, 0.70)


def flatten_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flat: list[dict[str, Any]] = []
    for step in steps or []:
        if step.get("kind") == "repeat":
            count = max(1, int(step.get("repeat_count") or 1))
            children = flatten_steps(step.get("steps") or [])
            for _ in range(count):
                flat.extend(children)
        else:
            flat.append(step)
    return flat


def estimate_workout(
    steps: list[dict[str, Any]],
    *,
    threshold_pace_s_per_km: int | None = None,
) -> dict[str, Any]:
    flat = flatten_steps(steps)
    total_dur = 0
    total_dist = 0
    total_tss = 0.0
    blocks: list[dict[str, Any]] = []

    default_pace = float(threshold_pace_s_per_km or 300)  # 5:00/km par défaut
    for idx, step in enumerate(flat):
        dur_s = step.get("duration_s")
        dist_m = step.get("distance_m")
        reps = step.get("reps")
        if_val = _step_intensity_factor(step)

        if dur_s is not None:
            d_s = int(dur_s)
        elif dist_m is not None:
            d_s = max(1, round((float(dist_m) / 1000.0) * (default_pace / max(0.4, if_val))))
        elif reps is not None:
            d_s = max(15, int(reps) * 4)
        else:
            d_s = 300

        total_dur += d_s
        if dist_m is not None:
            total_dist += int(dist_m)

        step_tss = (float(d_s) / 3600.0) * (if_val**2) * 100.0
        total_tss += step_tss
        blocks.append(
            {
                "index": idx,
                "kind": step.get("kind", "active"),
                "duration_s": d_s,
                "distance_m": dist_m,
                "reps": reps,
                "target_type": step.get("target_type", "open"),
                "target_min": step.get("target_min"),
                "target_max": step.get("target_max"),
                "intensity_pct": round(if_val * 100.0, 1),
            }
        )

    return {
        "estimated_duration_s": total_dur,
        "estimated_distance_m": total_dist,
        "estimated_tss": round(total_tss, 1),
        "blocks": blocks,
    }
