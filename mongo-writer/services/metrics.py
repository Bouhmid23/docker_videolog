"""Moteur de calcul des métriques d'endurance (NP, IF, TSS, hrTSS, rTSS, TRIMP, zones, mean-max)."""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from services.zones import build_hr_zones, build_power_zones

MEAN_MAX_DURATIONS_S = (1, 5, 10, 30, 60, 180, 300, 600, 1200, 1800, 3600)


def _to_float_array(values: list[float | int | None] | None) -> np.ndarray:
    if not values:
        return np.array([], dtype=np.float64)
    return np.array(
        [float(v) if v is not None else np.nan for v in values], dtype=np.float64
    )


def compute_normalized_power(power_arr: np.ndarray) -> float | None:
    valid = power_arr[~np.isnan(power_arr)]
    if valid.size == 0:
        return None
    # Remplacer les NaN isolés par 0 pour le calcul sur fenêtre glissante de 30 s
    clean = np.where(np.isnan(power_arr), 0.0, np.maximum(0.0, power_arr))
    if clean.size < 30:
        return round(float(np.mean(valid)), 1)
    kernel = np.ones(30, dtype=np.float64) / 30.0
    rolling = np.convolve(clean, kernel, mode="valid")
    np_val = float(np.mean(np.power(rolling, 4.0)) ** 0.25)
    return round(np_val, 1)


def compute_mean_max_curve(
    arr: np.ndarray, durations: tuple[int, ...] = MEAN_MAX_DURATIONS_S
) -> dict[str, float]:
    if arr.size == 0:
        return {}
    clean = np.where(np.isnan(arr), 0.0, np.maximum(0.0, arr))
    csum = np.concatenate(([0.0], np.cumsum(clean)))
    curve: dict[str, float] = {}
    n = clean.size
    for d in durations:
        if n >= d:
            window_means = (csum[d:] - csum[:-d]) / float(d)
            curve[str(d)] = round(float(np.max(window_means)), 1)
    return curve


def compute_trimp(
    hr_arr: np.ndarray, hr_rest: int, hr_max: int
) -> float | None:
    valid = hr_arr[~np.isnan(hr_arr)]
    if valid.size == 0 or hr_max <= hr_rest:
        return None
    denom = float(hr_max - hr_rest)
    hrr = np.clip((valid - float(hr_rest)) / denom, 0.0, 1.2)
    # Formule de Banister (pas de 1 s -> / 60 min)
    trimp = float(np.sum((1.0 / 60.0) * hrr * 0.64 * np.exp(1.92 * hrr)))
    return round(trimp, 1)


def compute_hr_tss(
    duration_s: float,
    hr_arr: np.ndarray,
    trimp: float | None,
    lthr_bpm: int | None,
    hr_rest: int,
    hr_max: int,
) -> float | None:
    valid = hr_arr[~np.isnan(hr_arr)]
    if valid.size == 0 or duration_s <= 0:
        return None
    if lthr_bpm and lthr_bpm > hr_rest and hr_max > lthr_bpm and trimp is not None:
        lthr_hrr = (float(lthr_bpm) - float(hr_rest)) / float(hr_max - hr_rest)
        trimp_1h_lthr = 60.0 * lthr_hrr * 0.64 * math.exp(1.92 * lthr_hrr)
        if trimp_1h_lthr > 0:
            return round((trimp / trimp_1h_lthr) * 100.0, 1)
    ref = float(lthr_bpm) if lthr_bpm and lthr_bpm > 0 else float(hr_max) * 0.85
    if ref <= 0:
        return None
    avg_hr = float(np.mean(valid))
    ratio = avg_hr / ref
    return round((duration_s / 3600.0) * (ratio**2) * 100.0, 1)


def compute_elevation_gain(alt_arr: np.ndarray) -> float | None:
    valid = alt_arr[~np.isnan(alt_arr)]
    if valid.size < 2:
        return None
    # Lissage léger (fenêtre 5s) pour filtrer le bruit barométrique/GPS
    if valid.size >= 5:
        smoothed = np.convolve(valid, np.ones(5) / 5.0, mode="valid")
    else:
        smoothed = valid
    diffs = np.diff(smoothed)
    gain = float(np.sum(diffs[diffs > 0.0]))
    return round(gain, 1)


def compute_time_in_zones(
    arr: np.ndarray, zones: list[dict[str, Any]], min_key: str, max_key: str
) -> dict[str, int]:
    valid = arr[~np.isnan(arr)]
    if valid.size == 0 or not zones:
        return {}
    dist: dict[str, int] = {}
    for z in zones:
        lo = z[min_key]
        hi = z[max_key]
        if hi is None:
            count = int(np.sum(valid >= lo))
        elif lo == 0:
            count = int(np.sum(valid < hi))
        else:
            count = int(np.sum((valid >= lo) & (valid < hi)))
        dist[z["zone"]] = count
    return dist


def compute_activity_metrics(
    streams: dict[str, list[Any]],
    *,
    duration_s: float,
    distance_m: float,
    sport: str,
    thresholds: dict[str, Any],
    rpe: int | float | None = None,
) -> dict[str, Any]:
    """Calcule toutes les métriques agrégées d'une activité à partir de ses flux 1 Hz."""
    power_arr = _to_float_array(streams.get("power"))
    hr_arr = _to_float_array(streams.get("hr"))
    speed_arr = _to_float_array(streams.get("speed"))  # km/h
    cadence_arr = _to_float_array(streams.get("cadence"))
    alt_arr = _to_float_array(streams.get("alt"))

    valid_power = power_arr[~np.isnan(power_arr)]
    valid_hr = hr_arr[~np.isnan(hr_arr)]
    valid_speed = speed_arr[~np.isnan(speed_arr)]
    valid_cad = cadence_arr[~np.isnan(cadence_arr)]

    avg_power = round(float(np.mean(valid_power)), 1) if valid_power.size > 0 else None
    max_power = round(float(np.max(valid_power)), 1) if valid_power.size > 0 else None
    np_w = compute_normalized_power(power_arr)

    avg_hr = round(float(np.mean(valid_hr)), 1) if valid_hr.size > 0 else None
    max_hr = int(round(float(np.max(valid_hr)))) if valid_hr.size > 0 else None

    avg_speed = round(float(np.mean(valid_speed)), 2) if valid_speed.size > 0 else None
    max_speed = round(float(np.max(valid_speed)), 2) if valid_speed.size > 0 else None
    avg_cadence = round(float(np.mean(valid_cad)), 1) if valid_cad.size > 0 else None

    ftp_w = thresholds.get("ftp_w")
    lthr_bpm = thresholds.get("lthr_bpm")
    hr_max = thresholds.get("hr_max") or 185
    hr_rest = thresholds.get("hr_rest") or 50
    tp_s_km = thresholds.get("threshold_pace_s_per_km")
    pace_500m_s = thresholds.get("pace_500m_s")

    intensity_factor = None
    power_tss = None
    if np_w is not None and ftp_w and ftp_w > 0 and duration_s > 0:
        intensity_factor = round(np_w / float(ftp_w), 3)
        power_tss = round(
            (duration_s * np_w * (np_w / float(ftp_w))) / (float(ftp_w) * 3600.0) * 100.0,
            1,
        )

    trimp = compute_trimp(hr_arr, int(hr_rest), int(hr_max))
    hr_tss = compute_hr_tss(duration_s, hr_arr, trimp, lthr_bpm, int(hr_rest), int(hr_max))

    avg_pace_s_km = None
    rtss = None
    if distance_m > 0 and duration_s > 0:
        avg_pace_s_km = round(duration_s / (distance_m / 1000.0), 1)
    elif avg_speed and avg_speed > 0:
        avg_pace_s_km = round(3600.0 / avg_speed, 1)

    if sport in ("course à pied", "tapis_course") and avg_pace_s_km and tp_s_km and tp_s_km > 0 and duration_s > 0:
        if_run = float(tp_s_km) / float(avg_pace_s_km)
        rtss = round((duration_s / 3600.0) * (if_run**2) * 100.0, 1)
        if intensity_factor is None:
            intensity_factor = round(if_run, 3)
    elif sport in ("aviron", "aviron_indoor", "skierg") and avg_pace_s_km and pace_500m_s and pace_500m_s > 0 and duration_s > 0:
        actual_500m_s = float(avg_pace_s_km) / 2.0
        if actual_500m_s > 0:
            if_row = float(pace_500m_s) / actual_500m_s
            rtss = round((duration_s / 3600.0) * (if_row**2) * 100.0, 1)
            if intensity_factor is None:
                intensity_factor = round(if_row, 3)

    rpe_tss = None
    if rpe is not None and duration_s > 0:
        if_rpe = max(0.35, min(1.3, 0.35 + float(rpe) * 0.08))
        rpe_tss = round((duration_s / 3600.0) * (if_rpe**2) * 100.0, 1)
        if intensity_factor is None:
            intensity_factor = round(if_rpe, 3)

    if power_tss is not None:
        tss, tss_method = power_tss, "power"
    elif rtss is not None:
        tss, tss_method = rtss, "pace"
    elif hr_tss is not None:
        tss, tss_method = hr_tss, "hr"
    elif rpe_tss is not None:
        tss, tss_method = rpe_tss, "rpe"
    else:
        tss, tss_method = None, None

    power_zones = build_power_zones(ftp_w)
    hr_zones = build_hr_zones(lthr_bpm, hr_max)

    return {
        "avg_power": avg_power,
        "max_power": max_power,
        "normalized_power": np_w,
        "intensity_factor": intensity_factor,
        "tss": tss,
        "tss_method": tss_method,
        "power_tss": power_tss,
        "hr_tss": hr_tss,
        "rtss": rtss,
        "trimp": trimp,
        "avg_hr": avg_hr,
        "max_hr": max_hr,
        "avg_speed_kmh": avg_speed,
        "max_speed_kmh": max_speed,
        "avg_pace_s_per_km": avg_pace_s_km,
        "avg_cadence": avg_cadence,
        "elevation_gain_m": compute_elevation_gain(alt_arr),
        "time_in_power_zones": compute_time_in_zones(
            power_arr, power_zones, "min_w", "max_w"
        ),
        "time_in_hr_zones": compute_time_in_zones(
            hr_arr, hr_zones, "min_bpm", "max_bpm"
        ),
        "mean_max_power": compute_mean_max_curve(power_arr),
        "mean_max_hr": compute_mean_max_curve(hr_arr),
    }
