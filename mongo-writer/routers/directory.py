"""Routes /v1/sports et /v1/directory/* : annuaire mobile des clubs et coachs avec détails sports."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from auth_firebase import Account, current_account
import db as db_mod
from services.sports_catalog import (
    build_org_sports_details,
    get_sport_meta,
    list_sports_catalog,
)

router = APIRouter(tags=["directory"])


def _serialize_dt(val: Any) -> str | None:
    if isinstance(val, datetime):
        return val.isoformat()
    return val if isinstance(val, str) else None


def _build_public_coach(uid: str, role: str, user_doc: dict[str, Any]) -> dict[str, Any]:
    fn = (user_doc.get("first_name") or "").strip()
    ln = (user_doc.get("last_name") or "").strip()
    name = f"{fn} {ln}".strip() or "Coach"
    cp = user_doc.get("coach_profile") or {}
    raw_sports = cp.get("sports") or []
    sports_out: list[dict[str, Any]] = []
    for entry in raw_sports:
        if not isinstance(entry, dict):
            continue
        code = (entry.get("sport") or "").strip().lower()
        if not code:
            continue
        meta = get_sport_meta(code)
        sports_out.append(
            {
                "code": meta["code"],
                "label": meta["label"],
                "is_indoor": meta["is_indoor"],
                "category": meta["category"],
                "level": entry.get("level") or "loisir",
                "years_experience": entry.get("years_experience"),
            }
        )
    return {
        "uid": uid,
        "name": name,
        "role": role,
        "photo_url": user_doc.get("photo_url"),
        "certifications": cp.get("certifications"),
        "bio": cp.get("bio"),
        "sports": sports_out,
    }


async def _hydrate_directory_orgs(
    org_docs: list[dict[str, Any]],
    account: Account,
) -> list[dict[str, Any]]:
    if not org_docs:
        return []

    org_ids = [o["_id"] for o in org_docs]

    # 1. Batch memberships actives de ces organisations (pour compteurs & coachs)
    mems = await db_mod.memberships_col.find(
        {"org_id": {"$in": org_ids}, "status": "active"},
        {"org_id": 1, "uid": 1, "role": 1},
    ).to_list(length=5000)

    athletes_count_by_org: dict[str, int] = {}
    staff_by_org: dict[str, list[dict[str, Any]]] = {}
    staff_uids: set[str] = set()
    my_role_by_org: dict[str, str] = {m.org_id: m.role for m in account.memberships}

    for m in mems:
        oid = m["org_id"]
        role = m.get("role")
        if role == "athlete":
            athletes_count_by_org[oid] = athletes_count_by_org.get(oid, 0) + 1
        elif role in {"owner", "admin", "coach"}:
            staff_by_org.setdefault(oid, []).append(m)
            if m.get("uid"):
                staff_uids.add(m["uid"])

    # 2. Batch profils utilisateurs des coachs/owners (sans PII privée)
    users_by_uid: dict[str, dict[str, Any]] = {}
    if staff_uids:
        async for u in db_mod.users_col.find(
            {"_id": {"$in": list(staff_uids)}},
            {"first_name": 1, "last_name": 1, "photo_url": 1, "coach_profile": 1},
        ):
            users_by_uid[u["_id"]] = u

    # 3. Batch groupes des organisations
    groups_by_org: dict[str, list[dict[str, Any]]] = {}
    async for g in db_mod.groups_col.find({"org_id": {"$in": org_ids}}).sort("name", 1):
        meta = get_sport_meta(g.get("sport"))
        groups_by_org.setdefault(g["org_id"], []).append(
            {
                "id": g["_id"],
                "name": g["name"],
                "sport": meta["code"],
                "sport_label": meta["label"],
                "is_indoor": meta["is_indoor"],
                "category": meta["category"],
                "coach_uids": list(g.get("coach_uids") or []),
            }
        )

    # 4. Batch demandes d'adhésion en attente de l'utilisateur connecté
    pending_req_by_org: dict[str, str] = {}
    async for r in db_mod.join_requests_col.find(
        {"uid": account.uid, "org_id": {"$in": org_ids}, "status": "pending"},
        {"_id": 1, "org_id": 1},
    ):
        pending_req_by_org[r["org_id"]] = r["_id"]

    results: list[dict[str, Any]] = []
    for org in org_docs:
        oid = org["_id"]
        org_type = org.get("type") or "club"
        org_groups = groups_by_org.get(oid, [])
        coaches_out: list[dict[str, Any]] = []
        seen_coach_uids: set[str] = set()

        for sm in staff_by_org.get(oid, []):
            uid = sm.get("uid")
            if not uid or uid in seen_coach_uids:
                continue
            u = users_by_uid.get(uid, {})
            cp = u.get("coach_profile") or {}
            has_cp = bool(cp.get("sports") or cp.get("certifications") or cp.get("bio"))
            if sm.get("role") == "coach" or org_type == "solo_coach" or has_cp:
                seen_coach_uids.add(uid)
                coaches_out.append(_build_public_coach(uid, sm.get("role") or "coach", u))

        sport_codes, sports_details = build_org_sports_details(
            org.get("sports") or [],
            groups=org_groups,
            coaches=coaches_out,
        )

        # Pour un solo_coach, si la description org est vide, fallback sur la bio du coach
        description = org.get("description")
        if not description and org_type == "solo_coach" and coaches_out:
            description = coaches_out[0].get("bio")

        results.append(
            {
                "id": oid,
                "type": org_type,
                "name": org.get("name"),
                "description": description,
                "city": org.get("city"),
                "country": org.get("country", "FR"),
                "logo_url": org.get("logo_url"),
                "accepting_join_requests": org.get("accepting_join_requests", True) is not False,
                "sports": sport_codes,
                "sports_details": sports_details,
                "has_indoor_sports": any(s["is_indoor"] for s in sports_details),
                "coaches": coaches_out,
                "groups": org_groups,
                "counts": {
                    "athletes": athletes_count_by_org.get(oid, 0),
                    "coaches": len(coaches_out),
                    "groups": len(org_groups),
                },
                "is_member": oid in my_role_by_org,
                "my_role": my_role_by_org.get(oid),
                "pending_request_id": pending_req_by_org.get(oid),
                "created_at": _serialize_dt(org.get("created_at")),
            }
        )

    return results


@router.get("/v1/sports")
async def get_sports_catalog(
    is_indoor: bool | None = Query(default=None),
    category: Literal["ergo", "strength", "skill", "outdoor"] | None = Query(default=None),
):
    """Retourne le référentiel complet des sports supportés (indoor/outdoor et capacités)."""
    sports = list_sports_catalog(is_indoor=is_indoor, category=category)
    return {"sports": sports, "count": len(sports)}


@router.get("/v1/directory/organizations")
async def list_directory_organizations(
    type: Literal["club", "solo_coach"] | None = Query(default=None),
    sport: str | None = Query(default=None),
    indoor_only: bool = Query(default=False),
    city: str | None = Query(default=None),
    q: str | None = Query(default=None, max_length=100),
    accepting_only: bool = Query(default=False),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    account: Account = Depends(current_account),
):
    """Annuaire mobile des clubs et coachs publics avec détails complets de leurs sports."""
    query: dict[str, Any] = {
        "status": "active",
        "is_public": {"$ne": False},
    }
    if type:
        query["type"] = type
    if accepting_only:
        query["accepting_join_requests"] = {"$ne": False}

    raw_orgs = await db_mod.orgs_col.find(query).sort("name", 1).to_list(length=500)
    hydrated = await _hydrate_directory_orgs(raw_orgs, account)

    sport_filter = sport.strip().lower() if sport and sport.strip() else None
    city_filter = city.strip().lower() if city and city.strip() else None
    q_filter = q.strip().lower() if q and q.strip() else None

    filtered: list[dict[str, Any]] = []
    for item in hydrated:
        if sport_filter and sport_filter not in item["sports"]:
            continue
        if indoor_only and not item["has_indoor_sports"]:
            continue
        if city_filter and city_filter not in (item.get("city") or "").lower():
            continue
        if q_filter:
            haystack_parts = [
                item.get("name") or "",
                item.get("city") or "",
                item.get("description") or "",
                *(s["label"] for s in item["sports_details"]),
                *(c.get("name") or "" for c in item["coaches"]),
                *(c.get("bio") or "" for c in item["coaches"]),
                *(c.get("certifications") or "" for c in item["coaches"]),
            ]
            haystack = " ".join(haystack_parts).lower()
            if q_filter not in haystack:
                continue
        filtered.append(item)

    total = len(filtered)
    page = filtered[offset : offset + limit]
    return {
        "organizations": page,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/v1/directory/organizations/{org_id}")
async def get_directory_organization_detail(
    org_id: str,
    account: Account = Depends(current_account),
):
    """Fiche détaillée publique d'un club ou d'un coach avec tous les sports, groupes et coachs."""
    org = await db_mod.orgs_col.find_one({"_id": org_id, "status": "active"})
    if not org:
        raise HTTPException(status_code=404, detail="Club ou coach introuvable.")
    is_member = account.membership(org_id) is not None
    if org.get("is_public", True) is False and not is_member and not account.platform_admin:
        raise HTTPException(status_code=404, detail="Club ou coach introuvable.")

    hydrated = await _hydrate_directory_orgs([org], account)
    item = hydrated[0]

    latest_req = await db_mod.join_requests_col.find_one(
        {"org_id": org_id, "uid": account.uid},
        sort=[("created_at", -1)],
    )
    my_req = None
    if latest_req:
        my_req = {
            "id": latest_req["_id"],
            "status": latest_req["status"],
            "requested_sports": latest_req.get("requested_sports", []),
            "requested_group_id": latest_req.get("requested_group_id"),
            "target_coach_uid": latest_req.get("target_coach_uid"),
            "message": latest_req.get("message"),
            "decision_note": latest_req.get("decision_note"),
            "created_at": _serialize_dt(latest_req.get("created_at")),
            "decided_at": _serialize_dt(latest_req.get("decided_at")),
        }

    return {"organization": item, "my_join_request": my_req}
