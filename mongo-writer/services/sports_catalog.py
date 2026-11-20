"""Référentiel backend des sports (métadonnées indoor/outdoor, capacités, agrégation org)."""
from __future__ import annotations

from typing import Any

SPORT_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "code": "cyclisme",
        "label": "Cyclisme (route / VTT)",
        "is_indoor": False,
        "category": "outdoor",
        "cadence_unit": "rpm",
        "cadence_max": 120,
        "supports_power": True,
        "supports_gps": True,
        "supports_reps": False,
        "supports_pace_km": False,
        "supports_pace_500m": False,
        "supports_css": False,
    },
    {
        "code": "home_trainer",
        "label": "Home-trainer / Cyclisme indoor",
        "is_indoor": True,
        "category": "ergo",
        "cadence_unit": "rpm",
        "cadence_max": 120,
        "supports_power": True,
        "supports_gps": False,
        "supports_reps": False,
        "supports_pace_km": False,
        "supports_pace_500m": False,
        "supports_css": False,
    },
    {
        "code": "course à pied",
        "label": "Course à pied",
        "is_indoor": False,
        "category": "outdoor",
        "cadence_unit": "spm",
        "cadence_max": 200,
        "supports_power": True,
        "supports_gps": True,
        "supports_reps": False,
        "supports_pace_km": True,
        "supports_pace_500m": False,
        "supports_css": False,
    },
    {
        "code": "tapis_course",
        "label": "Tapis de course (indoor)",
        "is_indoor": True,
        "category": "ergo",
        "cadence_unit": "spm",
        "cadence_max": 200,
        "supports_power": True,
        "supports_gps": False,
        "supports_reps": False,
        "supports_pace_km": True,
        "supports_pace_500m": False,
        "supports_css": False,
    },
    {
        "code": "aviron_indoor",
        "label": "Rameur / Aviron indoor",
        "is_indoor": True,
        "category": "ergo",
        "cadence_unit": "spm",
        "cadence_max": 45,
        "supports_power": True,
        "supports_gps": False,
        "supports_reps": False,
        "supports_pace_km": False,
        "supports_pace_500m": True,
        "supports_css": False,
    },
    {
        "code": "skierg",
        "label": "SkiErg (indoor)",
        "is_indoor": True,
        "category": "ergo",
        "cadence_unit": "spm",
        "cadence_max": 60,
        "supports_power": True,
        "supports_gps": False,
        "supports_reps": False,
        "supports_pace_km": False,
        "supports_pace_500m": True,
        "supports_css": False,
    },
    {
        "code": "musculation",
        "label": "Musculation & Force (indoor)",
        "is_indoor": True,
        "category": "strength",
        "cadence_unit": "reps/min",
        "cadence_max": 60,
        "supports_power": False,
        "supports_gps": False,
        "supports_reps": True,
        "supports_pace_km": False,
        "supports_pace_500m": False,
        "supports_css": False,
    },
    {
        "code": "cross_training",
        "label": "Cross-Training / HIIT (indoor)",
        "is_indoor": True,
        "category": "strength",
        "cadence_unit": "rpm",
        "cadence_max": 120,
        "supports_power": True,
        "supports_gps": False,
        "supports_reps": True,
        "supports_pace_km": True,
        "supports_pace_500m": True,
        "supports_css": False,
    },
    {
        "code": "yoga_pilates",
        "label": "Yoga / Pilates / Mobilité (indoor)",
        "is_indoor": True,
        "category": "skill",
        "cadence_unit": "reps/min",
        "cadence_max": 30,
        "supports_power": False,
        "supports_gps": False,
        "supports_reps": True,
        "supports_pace_km": False,
        "supports_pace_500m": False,
        "supports_css": False,
    },
    {
        "code": "boxe_combat",
        "label": "Boxe & Arts martiaux (indoor)",
        "is_indoor": True,
        "category": "skill",
        "cadence_unit": "reps/min",
        "cadence_max": 120,
        "supports_power": False,
        "supports_gps": False,
        "supports_reps": True,
        "supports_pace_km": False,
        "supports_pace_500m": False,
        "supports_css": False,
    },
    {
        "code": "escalade",
        "label": "Escalade indoor / Bloc",
        "is_indoor": True,
        "category": "skill",
        "cadence_unit": "reps/min",
        "cadence_max": 30,
        "supports_power": False,
        "supports_gps": False,
        "supports_reps": True,
        "supports_pace_km": False,
        "supports_pace_500m": False,
        "supports_css": False,
    },
    {
        "code": "natation",
        "label": "Natation (bassin / eau libre)",
        "is_indoor": True,
        "category": "outdoor",
        "cadence_unit": "spm",
        "cadence_max": 90,
        "supports_power": False,
        "supports_gps": True,
        "supports_reps": False,
        "supports_pace_km": False,
        "supports_pace_500m": False,
        "supports_css": True,
    },
    {
        "code": "triathlon",
        "label": "Triathlon",
        "is_indoor": False,
        "category": "outdoor",
        "cadence_unit": "rpm",
        "cadence_max": 120,
        "supports_power": True,
        "supports_gps": True,
        "supports_reps": False,
        "supports_pace_km": True,
        "supports_pace_500m": False,
        "supports_css": True,
    },
    {
        "code": "aviron",
        "label": "Aviron (bateau)",
        "is_indoor": False,
        "category": "outdoor",
        "cadence_unit": "spm",
        "cadence_max": 45,
        "supports_power": True,
        "supports_gps": True,
        "supports_reps": False,
        "supports_pace_km": False,
        "supports_pace_500m": True,
        "supports_css": False,
    },
    {
        "code": "football",
        "label": "Football",
        "is_indoor": False,
        "category": "outdoor",
        "cadence_unit": "spm",
        "cadence_max": 180,
        "supports_power": False,
        "supports_gps": True,
        "supports_reps": False,
        "supports_pace_km": True,
        "supports_pace_500m": False,
        "supports_css": False,
    },
    {
        "code": "rugby",
        "label": "Rugby",
        "is_indoor": False,
        "category": "outdoor",
        "cadence_unit": "spm",
        "cadence_max": 180,
        "supports_power": False,
        "supports_gps": True,
        "supports_reps": False,
        "supports_pace_km": True,
        "supports_pace_500m": False,
        "supports_css": False,
    },
    {
        "code": "autre",
        "label": "Autre",
        "is_indoor": False,
        "category": "outdoor",
        "cadence_unit": "rpm",
        "cadence_max": 120,
        "supports_power": True,
        "supports_gps": True,
        "supports_reps": True,
        "supports_pace_km": True,
        "supports_pace_500m": False,
        "supports_css": False,
    },
)

SPORT_CATALOG_BY_CODE: dict[str, dict[str, Any]] = {
    item["code"]: item for item in SPORT_CATALOG
}

LEVEL_ORDER: dict[str, int] = {
    "loisir": 0,
    "compétition régionale": 1,
    "nationale": 2,
    "élite": 3,
}


def get_sport_meta(code: str | None) -> dict[str, Any]:
    """Retourne les métadonnées complètes d'un sport (fallback sur 'autre')."""
    if not code:
        return dict(SPORT_CATALOG_BY_CODE["autre"])
    norm = code.strip().lower()
    meta = SPORT_CATALOG_BY_CODE.get(norm)
    if meta:
        return dict(meta)
    fallback = dict(SPORT_CATALOG_BY_CODE["autre"])
    fallback["code"] = norm
    fallback["label"] = code
    return fallback


def list_sports_catalog(
    *,
    is_indoor: bool | None = None,
    category: str | None = None,
) -> list[dict[str, Any]]:
    items = [dict(item) for item in SPORT_CATALOG]
    if is_indoor is not None:
        items = [s for s in items if s["is_indoor"] is is_indoor]
    if category:
        cat = category.strip().lower()
        items = [s for s in items if s["category"] == cat]
    return items


def build_org_sports_details(
    org_sports: list[str] | None,
    groups: list[dict[str, Any]] | None = None,
    coaches: list[dict[str, Any]] | None = None,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Fusionne les sports de l'organisation, de ses groupes et de ses coachs.

    Retourne `(sport_codes, sports_details)` où chaque sport contient ses métadonnées
    techniques complètes, les niveaux encadrés, l'expérience max, les groupes et coachs.
    """
    ordered_codes: list[str] = []
    seen: set[str] = set()

    def _register(code: str | None) -> str | None:
        if not code or not isinstance(code, str):
            return None
        norm = code.strip().lower()
        if not norm:
            return None
        if norm not in seen:
            seen.add(norm)
            ordered_codes.append(norm)
        return norm

    for s in org_sports or []:
        _register(s)

    groups_by_sport: dict[str, list[dict[str, Any]]] = {}
    for g in groups or []:
        sport_code = _register(g.get("sport"))
        if not sport_code:
            continue
        groups_by_sport.setdefault(sport_code, []).append(
            {
                "id": g["id"],
                "name": g["name"],
                "sport": sport_code,
                "coach_uids": list(g.get("coach_uids") or []),
            }
        )

    coaches_by_sport: dict[str, list[dict[str, Any]]] = {}
    levels_by_sport: dict[str, set[str]] = {}
    max_exp_by_sport: dict[str, int | None] = {}

    for c in coaches or []:
        for sp in c.get("sports") or []:
            sport_code = _register(sp.get("code") or sp.get("sport"))
            if not sport_code:
                continue
            lvl = sp.get("level")
            exp = sp.get("years_experience")
            if lvl:
                levels_by_sport.setdefault(sport_code, set()).add(lvl)
            if isinstance(exp, int):
                prev = max_exp_by_sport.get(sport_code)
                if prev is None or exp > prev:
                    max_exp_by_sport[sport_code] = exp
            coaches_by_sport.setdefault(sport_code, []).append(
                {
                    "uid": c["uid"],
                    "name": c["name"],
                    "level": lvl,
                    "years_experience": exp,
                }
            )

    details: list[dict[str, Any]] = []
    for code in ordered_codes:
        meta = get_sport_meta(code)
        lvls = sorted(
            levels_by_sport.get(code, set()),
            key=lambda x: LEVEL_ORDER.get(x, 99),
        )
        details.append(
            {
                **meta,
                "levels": lvls,
                "max_years_experience": max_exp_by_sport.get(code),
                "groups": groups_by_sport.get(code, []),
                "coaches": coaches_by_sport.get(code, []),
            }
        )

    return ordered_codes, details
