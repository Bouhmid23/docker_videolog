"""Calcul des zones d'entraînement (Coggan puissance, Friel FC, allure) et résolution des seuils datés."""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import db as db_mod


def build_power_zones(ftp_w: int | None) -> list[dict[str, Any]]:
    if not ftp_w or ftp_w <= 0:
        return []
    bounds = [
        ("Z1", "Récupération active", 0.0, 0.55),
        ("Z2", "Endurance", 0.55, 0.75),
        ("Z3", "Tempo", 0.75, 0.90),
        ("Z4", "Seuil (FTP)", 0.90, 1.05),
        ("Z5", "VO2max", 1.05, 1.20),
        ("Z6", "Capacité anaérobie", 1.20, 1.50),
        ("Z7", "Neuromusculaire", 1.50, None),
    ]
    zones = []
    for code, name, lo_pct, hi_pct in bounds:
        zones.append(
            {
                "zone": code,
                "name": name,
                "min_w": round(ftp_w * lo_pct),
                "max_w": round(ftp_w * hi_pct) if hi_pct is not None else None,
            }
        )
    return zones


def build_hr_zones(
    lthr_bpm: int | None, hr_max: int | None
) -> list[dict[str, Any]]:
    if lthr_bpm and lthr_bpm > 0:
        ref = float(lthr_bpm)
        specs = [
            ("Z1", "Récupération", 0.0, 0.81),
            ("Z2", "Aérobie", 0.81, 0.89),
            ("Z3", "Tempo", 0.89, 0.93),
            ("Z4", "Sous-seuil", 0.93, 1.00),
            ("Z5", "VO2max / Anaérobie", 1.00, None),
        ]
    elif hr_max and hr_max > 0:
        ref = float(hr_max)
        specs = [
            ("Z1", "Récupération", 0.0, 0.60),
            ("Z2", "Endurance", 0.60, 0.70),
            ("Z3", "Tempo", 0.70, 0.80),
            ("Z4", "Seuil", 0.80, 0.90),
            ("Z5", "Maximal", 0.90, None),
        ]
    else:
        return []

    return [
        {
            "zone": code,
            "name": name,
            "min_bpm": round(ref * lo_pct),
            "max_bpm": round(ref * hi_pct) if hi_pct is not None else None,
        }
        for code, name, lo_pct, hi_pct in specs
    ]


def build_pace_zones(threshold_pace_s_per_km: int | None) -> list[dict[str, Any]]:
    if not threshold_pace_s_per_km or threshold_pace_s_per_km <= 0:
        return []
    tp = float(threshold_pace_s_per_km)
    # Plus les s/km sont bas, plus l'allure est rapide
    specs = [
        ("Z1", "Récupération", 1.29, None),
        ("Z2", "Endurance", 1.14, 1.29),
        ("Z3", "Tempo", 1.06, 1.14),
        ("Z4", "Seuil", 0.99, 1.06),
        ("Z5", "VMA / VO2max", None, 0.99),
    ]
    return [
        {
            "zone": code,
            "name": name,
            "fast_s_per_km": round(tp * fast_pct) if fast_pct is not None else None,
            "slow_s_per_km": round(tp * slow_pct) if slow_pct is not None else None,
        }
        for code, name, slow_pct, fast_pct in specs
    ]


def serialize_threshold_doc(doc: dict[str, Any]) -> dict[str, Any]:
    eff = doc.get("effective_from")
    if isinstance(eff, datetime):
        eff_str = eff.date().isoformat()
    elif isinstance(eff, date):
        eff_str = eff.isoformat()
    else:
        eff_str = str(eff) if eff else None

    ftp_w = doc.get("ftp_w")
    lthr_bpm = doc.get("lthr_bpm")
    hr_max = doc.get("hr_max")
    tp = doc.get("threshold_pace_s_per_km")

    return {
        "id": str(doc.get("_id", "")),
        "org_id": doc.get("org_id"),
        "uid": doc.get("uid"),
        "sport": doc.get("sport", "cyclisme"),
        "effective_from": eff_str,
        "ftp_w": ftp_w,
        "lthr_bpm": lthr_bpm,
        "threshold_pace_s_per_km": tp,
        "pace_500m_s": doc.get("pace_500m_s"),
        "css_s_per_100m": doc.get("css_s_per_100m"),
        "hr_max": hr_max,
        "hr_rest": doc.get("hr_rest"),
        "zones_model": doc.get("zones_model", "coggan_7"),
        "power_zones": build_power_zones(ftp_w),
        "hr_zones": build_hr_zones(lthr_bpm, hr_max),
        "pace_zones": build_pace_zones(tp),
        "created_at": doc["created_at"].isoformat()
        if isinstance(doc.get("created_at"), datetime)
        else doc.get("created_at"),
    }


async def resolve_thresholds_for_date(
    uid: str,
    sport: str,
    ref_dt: datetime | date | None = None,
) -> dict[str, Any]:
    """Retourne les seuils applicables à une date donnée, avec repli sur le profil athlète."""
    if ref_dt is None:
        ref_iso = datetime.now(UTC).date().isoformat()
    elif isinstance(ref_dt, datetime):
        ref_iso = ref_dt.date().isoformat()
    else:
        ref_iso = ref_dt.isoformat()

    doc = await db_mod.threshold_history_col.find_one(
        {"uid": uid, "sport": sport, "effective_from": {"$lte": ref_iso}},
        sort=[("effective_from", -1), ("created_at", -1)],
    )
    if not doc and sport != "cyclisme":
        # Repli sur la dernière entrée tous sports confondus pour récupérer FC max/repos/LTHR
        doc = await db_mod.threshold_history_col.find_one(
            {"uid": uid, "effective_from": {"$lte": ref_iso}},
            sort=[("effective_from", -1), ("created_at", -1)],
        )

    result: dict[str, Any] = {
        "ftp_w": doc.get("ftp_w") if doc else None,
        "lthr_bpm": doc.get("lthr_bpm") if doc else None,
        "threshold_pace_s_per_km": doc.get("threshold_pace_s_per_km") if doc else None,
        "pace_500m_s": doc.get("pace_500m_s") if doc else None,
        "css_s_per_100m": doc.get("css_s_per_100m") if doc else None,
        "hr_max": doc.get("hr_max") if doc else None,
        "hr_rest": doc.get("hr_rest") if doc else None,
    }

    if result["hr_max"] is None or result["hr_rest"] is None:
        user = await db_mod.users_col.find_one(
            {"_id": uid}, {"athlete_profile": 1, "health_consent": 1}
        )
        consent = (user or {}).get("health_consent") or {}
        profile = (user or {}).get("athlete_profile") or {}
        if consent.get("granted") is True:
            if result["hr_max"] is None:
                result["hr_max"] = profile.get("hr_max")
            if result["hr_rest"] is None:
                result["hr_rest"] = profile.get("hr_rest")

    return result
